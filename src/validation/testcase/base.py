# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import asyncio
import logging
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from github.GitRef import GitRef
from gitlab.v4.objects import ProjectBranch
from ogr.abstract import CommitFlag, GitProject, PullRequest
from ogr.exceptions import GithubAPIException
from ogr.services.github.check_run import GithubCheckRun

from validation.deployment import PRODUCTION_INFO, DeploymentInfo
from validation.helpers import copr, log_failure
from validation.utils.trigger import Trigger


class TestFailureError(Exception):
    """Exception raised when a test case fails with a specific failure message."""


class Testcase(ABC):
    CHECK_TIME_FOR_REACTION = 2  # minutes - time to wait for commit statuses to be set to pending
    CHECK_TIME_FOR_SUBMIT_BUILDS = 5  # minutes - time to wait for build to be submitted in Copr
    CHECK_TIME_FOR_BUILD = 60  # minutes - time to wait for build to complete
    CHECK_TIME_FOR_WATCH_STATUSES = 60  # minutes - time to watch for commit statuses
    POLLING_INTERVAL = 2  # minutes - interval between status/build checks
    PACKIT_YAML_PATH = ".packit.yaml"
    MAX_COMMENTS_TO_CHECK = 5  # Limit comment fetching to avoid excessive API calls
    HTTP_FORBIDDEN = 403  # HTTP status code for forbidden/access denied

    def __init__(
        self,
        project: GitProject,
        pr: PullRequest | None = None,
        trigger: Trigger = Trigger.pr_opened,
        deployment: DeploymentInfo | None = None,
        comment: str | None = None,
        existing_prs: list | None = None,
    ):
        self.project = project
        self.pr = pr
        self.pr_branch_ref: Optional[Union[ProjectBranch, GitRef]] = None
        self.failure_msg = ""
        self.trigger = trigger
        self.head_commit = pr.head_commit if pr else None
        self._copr_project_name = None
        self.deployment = deployment or PRODUCTION_INFO
        self.comment = comment
        self._build = None
        self._statuses: list[GithubCheckRun | CommitFlag] = []
        self._build_triggered_at: datetime | None = None
        self._existing_prs = existing_prs  # Cache to avoid re-fetching in create_pr()

    @property
    def copr_project_name(self):
        """
        Get the name of Copr project from id of the PR.
        :return:
        """
        if self.pr and not self._copr_project_name:
            self._copr_project_name = self.construct_copr_project_name()
        return self._copr_project_name

    def _cleanup(self):
        """
        Hook for subclasses to perform cleanup after test completion.
        Called in finally block to ensure cleanup happens even if test fails.
        """

    @staticmethod
    def _ensure_aware_datetime(dt: datetime) -> datetime:
        """
        Convert a naive datetime to UTC-aware datetime.
        If already timezone-aware, return as-is.

        Args:
            dt: A datetime object (naive or aware)

        Returns:
            Timezone-aware datetime (assumes UTC for naive datetimes)
        """
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _check_for_error_comment(self) -> str | None:
        """
        Check for new error comments from packit-service.
        Uses generator to avoid fetching all comment pages.

        Returns:
            The comment body if a new error comment is found, None otherwise
        """
        if not self.pr:
            return None

        # Get comments in reverse order (newest first)
        # Generator is lazy - stops fetching pages once we find what we need
        for i, comment in enumerate(self.pr.get_comments(reverse=True)):
            if comment.author == self.account_name:
                # Found a comment from packit-service
                return comment.body
            # Only check first MAX_COMMENTS_TO_CHECK comments to avoid excessive API calls
            if i >= self.MAX_COMMENTS_TO_CHECK - 1:
                break

        return None

    async def run_test(self) -> bool:
        """
        Run all checks, if there is any failure message, send it to Sentry and in case of
        opening PR close it.

        Returns:
            bool: True if test passed, False if test failed
        """
        pr_id = f"PR#{self.pr.id}" if self.pr else "new PR"
        logging.info("Starting test for %s (%s trigger)", pr_id, self.trigger.value)
        test_passed = False
        try:
            await self.run_checks()
            if self.failure_msg:
                message = f"{self.pr.title} ({self.pr.url}) failed: {self.failure_msg}"
                logging.error("Test failed: %s", message)
                log_failure(message)
                # Raise exception with failure message for better error reporting
                raise TestFailureError(self.failure_msg.strip())
            logging.info("Test passed for %s", pr_id)
            test_passed = True

            if self.trigger == Trigger.pr_opened:
                logging.debug("Closing PR and deleting branch for %s", pr_id)
                self.pr.close()
                if self.pr_branch_ref:
                    self.pr_branch_ref.delete()
        except TestFailureError:
            # Re-raise TestFailureError to preserve the failure message
            raise
        except Exception as e:
            pr_info = f"{self.pr.title} ({self.pr.url})" if self.pr else "new PR"
            msg = f"Validation test {pr_info} failed: {e}"
            logging.error(msg)
            tb = traceback.format_exc()
            logging.error(tb)
            test_passed = False
        finally:
            self._cleanup()

        return test_passed

    def trigger_build(self):
        """
        Trigger the build (by commenting/pushing to the PR/opening a new PR).
        """
        logging.info(
            "Triggering a build for %s",
            self.pr if self.pr else "new PR",
        )
        if self.trigger == Trigger.comment:
            if not self.pr:
                msg = "Cannot post comment: PR is not set"
                raise ValueError(msg)

            comment = self.comment or self.deployment.pr_comment
            try:
                self.pr.comment(comment)
            except GithubAPIException as e:
                if e.response_code == self.HTTP_FORBIDDEN:
                    error_msg = (
                        f"Failed to post comment to PR {self.pr.url} (HTTP 403 Forbidden).\n"
                        "This typically means:\n"
                        "  1. The PR has reached GitHub's 2,500 comment limit and "
                        "commenting is disabled, OR\n"
                        "  2. The PR has been closed/locked by GitHub.\n"
                        f"Please check the PR at {self.pr.url} and verify its status.\n"
                        "If the PR has hit the comment limit, close it and create a "
                        "fresh test PR.\n"
                    )
                    logging.error(error_msg)
                    raise RuntimeError(error_msg) from e
                # Re-raise if it's a different error
                raise
        elif self.trigger == Trigger.push:
            self.push_to_pr()
        else:
            self.create_pr()

    def push_to_pr(self):
        """
        Push a new commit to the PR.
        """
        branch = self.pr.source_branch
        commit_msg = f"Commit build trigger ({datetime.now(tz=timezone.utc).strftime('%d/%m/%y')})"
        self.head_commit = self.create_empty_commit(branch, commit_msg)

    def create_pr(self):
        """
        Create a new PR, if the source branch 'test_case_opened_pr' does not exist,
        create one and commit some changes before it.
        """
        source_branch = f"test/{self.deployment.name}/opened_pr"
        pr_title = f"Basic test case ({self.deployment.name}): opened PR trigger"
        logging.info("Creating new PR: %s from branch %s", pr_title, source_branch)
        self.delete_previous_branch(source_branch)

        # Delete the PR from the previous test run if it exists.
        # Use cached PR list from constructor to avoid re-fetching
        if self._existing_prs is None:
            logging.debug("Fetching PR list to check for existing PR...")
            existing_prs = list(self.project.get_pr_list())
        else:
            logging.debug("Using cached PR list to check for existing PR")
            existing_prs = self._existing_prs

        existing_pr = [pr for pr in existing_prs if pr.title == pr_title]
        if len(existing_pr) == 1:
            logging.debug("Closing existing PR: %s", existing_pr[0].url)
            existing_pr[0].close()

        logging.debug("Creating file in new branch: %s", source_branch)
        self.create_file_in_new_branch(source_branch)
        if self.deployment.opened_pr_trigger__packit_yaml_fix:
            self.fix_packit_yaml(source_branch)

        logging.debug("Creating PR...")
        self.pr = self.project.create_pr(
            title=pr_title,
            body="This test case is triggered automatically by our validation script.",
            target_branch=self.project.default_branch,
            source_branch=source_branch,
        )
        self.head_commit = self.pr.head_commit
        logging.info("PR created: %s", self.pr.url)

    async def run_checks(self):
        """
        Run all checks of the test case.
        """
        # Check if this is a "skip build" test - if so, skip Copr checks
        skip_copr_checks = False
        if self.pr and "skip" in self.pr.title.lower() and "build" in self.pr.title.lower():
            skip_copr_checks = True
            logging.info("Detected 'skip build' test: %s - skipping Copr checks", self.pr.title)

        if skip_copr_checks:
            # For skip_build tests, trigger the build but don't wait for Copr submission/completion
            self._build_triggered_at = datetime.now(tz=timezone.utc)
            self.trigger_build()

            # Wait for packit-service to process the webhook and set statuses
            if self.trigger == Trigger.pr_opened:
                logging.debug("Waiting 30s for packit-service to receive webhook...")
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(5)

            # Check that statuses are set (should show build was skipped)
            await self.check_pending_check_runs()
        else:
            # Normal flow: check build submission and completion
            await self.check_build_submitted()

            if not self._build:
                return

            await self.check_build(self._build.id)

        await self.check_completed_statuses()
        self.check_comment()

    async def check_pending_check_runs(self):
        """
        Check whether some check run is set to queued
        (they are updated in loop, so it is enough).
        """
        status_names = [self.get_status_name(status) for status in self.get_statuses()]

        watch_end = datetime.now(tz=timezone.utc) + timedelta(minutes=self.CHECK_TIME_FOR_REACTION)
        failure_message = (
            f"Commit statuses were not set to pending in time "
            f"({self.CHECK_TIME_FOR_REACTION} minutes).\n"
        )

        # when a new PR is opened
        while len(status_names) == 0:
            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += failure_message
                return
            await asyncio.sleep(30)
            status_names = [self.get_status_name(status) for status in self.get_statuses()]

        logging.info(
            "Watching pending statuses for commit %s",
            self.head_commit,
        )

        # Small delay before entering polling loop to avoid rapid API calls
        await asyncio.sleep(5)

        while True:
            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += failure_message
                return

            new_statuses = [
                status
                for status in self.get_statuses()
                if self.get_status_name(status) in status_names
            ]

            for status in new_statuses:
                # check run / status can be in a short period time changed from queued
                # (Task was accepted) to in_progress, so check only that it doesn't
                # have completed status
                if not self.is_status_completed(status):
                    return

            await asyncio.sleep(self.POLLING_INTERVAL * 60)

    async def check_build_submitted(self):
        """
        Check whether the build was submitted in Copr in time.
        """
        # Only check for existing builds if PR already exists
        # For new PR test, there can't be any existing builds
        old_build_len = 0
        if self.pr and self.trigger != Trigger.pr_opened:
            try:
                old_build_len = len(
                    copr().build_proxy.get_list(self.deployment.copr_user, self.copr_project_name),
                )
            except Exception:
                old_build_len = 0

        self._build_triggered_at = datetime.now(tz=timezone.utc)
        self.trigger_build()

        # For new PR, wait longer to give packit-service time to receive webhook
        # and set up initial statuses before we start polling
        if self.trigger == Trigger.pr_opened:
            logging.debug("Waiting 30s for packit-service to receive webhook...")
            await asyncio.sleep(30)
        else:
            # For comment/push triggers, shorter wait is fine
            await asyncio.sleep(5)

        watch_end = datetime.now(tz=timezone.utc) + timedelta(
            minutes=self.CHECK_TIME_FOR_SUBMIT_BUILDS,
        )

        await self.check_pending_check_runs()

        logging.info(
            "Watching whether a build has been submitted for %s in %s",
            self.pr,
            self.copr_project_name,
        )
        while True:
            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += (
                    "The build was not submitted in Copr in time "
                    f"({self.CHECK_TIME_FOR_SUBMIT_BUILDS} minutes).\n"
                )
                return

            try:
                new_builds = copr().build_proxy.get_list(
                    self.deployment.copr_user,
                    self.copr_project_name,
                )
            except Exception as e:
                # project does not exist yet
                msg = f"Copr project doesn't exist yet: {e}"
                logging.debug(msg)
                await asyncio.sleep(30)
                continue

            if len(new_builds) >= old_build_len + 1:
                self._build = new_builds[0]
                return

            # Check for new error comments from packit-service after build was triggered
            if self.pr:
                error_comment = self._check_for_error_comment()
                if error_comment:
                    self.failure_msg += (
                        f"New comment from packit-service while submitting build: {error_comment}\n"
                    )

            await asyncio.sleep(120)

    async def check_build(self, build_id):
        """
        Check whether the build was successful in Copr.

        Args:
            build_id: ID of the Copr build
        """
        watch_end = datetime.now(tz=timezone.utc) + timedelta(minutes=self.CHECK_TIME_FOR_BUILD)
        state_reported = ""
        logging.info("Watching Copr build %s", build_id)

        while True:
            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += (
                    f"The build did not finish in time ({self.CHECK_TIME_FOR_BUILD} minutes).\n"
                )
                return

            build = copr().build_proxy.get(build_id)
            if build.state == state_reported:
                await asyncio.sleep(self.POLLING_INTERVAL * 60)
                continue
            state_reported = build.state

            if state_reported not in [
                "running",
                "pending",
                "starting",
                "forked",
                "importing",
                "waiting",
            ]:
                if state_reported != "succeeded":
                    self.failure_msg += (
                        f"The build in Copr was not successful. Copr state: {state_reported}.\n"
                    )
                return

            await asyncio.sleep(self.POLLING_INTERVAL * 60)

    def check_comment(self):
        """
        Check whether packit-service has commented when the Copr build was not successful.
        """
        failure = "The build in Copr was not successful." in self.failure_msg

        if failure and self.pr:
            # Check recent comments (newest first) using generator
            found_packit_comment = False
            for i, comment in enumerate(self.pr.get_comments(reverse=True)):
                if comment.author == self.account_name:
                    found_packit_comment = True
                    break
                # Only check first MAX_COMMENTS_TO_CHECK comments
                if i >= self.MAX_COMMENTS_TO_CHECK - 1:
                    break

            if not found_packit_comment:
                self.failure_msg += (
                    "No comment from packit-service about unsuccessful last Copr build found.\n"
                )

    def _get_packit_yaml_ref(self, branch: str) -> str:
        """
        Get the git ref to read .packit.yaml from.
        Can be overridden in subclasses (e.g., Pagure reads from default branch).
        """
        return branch

    def fix_packit_yaml(self, branch: str):
        """
        Update .packit.yaml file in the branch according to the deployment needs
        """
        ref = self._get_packit_yaml_ref(branch)
        packit_yaml_content = self.project.get_file_content(path=self.PACKIT_YAML_PATH, ref=ref)
        packit_yaml_content = packit_yaml_content.replace(
            self.deployment.opened_pr_trigger__packit_yaml_fix.from_str,
            self.deployment.opened_pr_trigger__packit_yaml_fix.to_str,
        )

        self.update_file_and_commit(
            path=self.PACKIT_YAML_PATH,
            commit_msg=self.deployment.opened_pr_trigger__packit_yaml_fix.git_msg,
            content=packit_yaml_content,
            branch=branch,
        )

    async def check_completed_statuses(self):
        """
        Check whether all check runs are set to success.
        """
        if "The build in Copr was not successful." in self.failure_msg:
            return

        await self.watch_statuses()
        for status in self._statuses:
            if not self.is_status_successful(status):
                self.failure_msg += (
                    f"Check run {self.get_status_name(status)} was set to failure.\n"
                )

    async def watch_statuses(self):
        """
        Watch the check runs, if all the check runs have completed status,
        return.
        """
        watch_end = datetime.now(tz=timezone.utc) + timedelta(
            minutes=self.CHECK_TIME_FOR_WATCH_STATUSES,
        )
        logging.info(
            "Watching statuses for commit %s",
            self.head_commit,
        )

        while True:
            all_statuses = self.get_statuses()
            # Filter to only recent statuses (created after build was triggered)
            self._statuses = [status for status in all_statuses if self.is_status_recent(status)]

            # Log if we filtered out any old statuses
            filtered_count = len(all_statuses) - len(self._statuses)
            if filtered_count > 0:
                logging.debug(
                    "Filtered out %d old status(es) from before build was triggered",
                    filtered_count,
                )

            # Only consider checks complete if we have statuses AND they're all done
            if self._statuses and all(
                self.is_status_completed(status) for status in self._statuses
            ):
                break

            if datetime.now(tz=timezone.utc) > watch_end:
                if not self._statuses:
                    self.failure_msg += (
                        "No commit statuses found after "
                        f"{self.CHECK_TIME_FOR_WATCH_STATUSES} minutes. "
                        "packit-service may not have responded to the PR.\n"
                    )
                else:
                    self.failure_msg += (
                        "These commit statuses were not completed in "
                        f"{self.CHECK_TIME_FOR_WATCH_STATUSES} minutes"
                        " after the build was submitted:\n"
                    )
                    for status in self._statuses:
                        if not self.is_status_completed(status):
                            self.failure_msg += f"{self.get_status_name(status)}\n"
                return

            await asyncio.sleep(self.POLLING_INTERVAL * 60)

    @property
    @abstractmethod
    def account_name(self) -> str:
        """
        Get the name of the (bot) account in GitHub/GitLab/Pagure.
        """

    @abstractmethod
    def construct_copr_project_name(self) -> str:
        """
        Construct the Copr project name from the PR.
        Used by GitHub/GitLab. Pagure overrides to raise NotImplementedError.
        """

    @abstractmethod
    def get_statuses(self) -> Union[list[GithubCheckRun], list[CommitFlag]]:
        """
        Get the statuses (checks in GitHub).
        """

    @abstractmethod
    def is_status_completed(self, status: Union[GithubCheckRun, CommitFlag]) -> bool:
        """
        Check whether the status is in completed state (e.g. success, failure).
        """

    @abstractmethod
    def is_status_successful(self, status: Union[GithubCheckRun, CommitFlag]) -> bool:
        """
        Check whether the status is in successful state.
        """

    @abstractmethod
    def is_status_recent(self, status: Union[GithubCheckRun, CommitFlag]) -> bool:
        """
        Check whether the status was created after the build was triggered.
        This filters out old statuses from previous test runs.
        """

    @abstractmethod
    def delete_previous_branch(self, ref: str):
        """
        Delete the branch from the previous test run if it exists.
        """

    @abstractmethod
    def create_file_in_new_branch(self, branch: str):
        """
        Create a new branch and a new file in it via API (creates new commit).
        """

    @abstractmethod
    def update_file_and_commit(self, path: str, commit_msg: str, content: str, branch: str):
        """
        Update a file via API (creates new commit).
        """

    @abstractmethod
    def get_status_name(self, status: Union[GithubCheckRun, CommitFlag]) -> str:
        """
        Get the name of the status/check that is visible to user.
        """

    @abstractmethod
    def create_empty_commit(self, branch: str, commit_msg: str) -> str:
        """
        Create an empty commit via API.
        """
