# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

from github.GitRef import GitRef
from gitlab.v4.objects import ProjectBranch
from ogr.abstract import CommitFlag, GitProject, PullRequest
from ogr.services.github.check_run import GithubCheckRun

from validation.deployment import PRODUCTION_INFO, DeploymentInfo
from validation.helpers import copr, log_failure
from validation.utils.trigger import Trigger


class Testcase:
    def __init__(
        self,
        project: GitProject,
        pr: PullRequest | None = None,
        trigger: Trigger = Trigger.pr_opened,
        deployment: DeploymentInfo | None = None,
        comment: str | None = None,
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

    @property
    def copr_project_name(self):
        """
        Get the name of Copr project from id of the PR.
        :return:
        """
        if self.pr and not self._copr_project_name:
            self._copr_project_name = self.construct_copr_project_name()
        return self._copr_project_name

    def run_test(self):
        """
        Run all checks, if there is any failure message, send it to Sentry and in case of
        opening PR close it.
        :return:
        """
        try:
            self.run_checks()
            if self.failure_msg:
                message = f"{self.pr.title} ({self.pr.url}) failed: {self.failure_msg}"

                log_failure(message)

            if self.trigger == Trigger.pr_opened:
                self.pr.close()
                self.pr_branch_ref.delete()
        except Exception as e:
            logging.error(f"Validation test {self.pr.title} ({self.pr.url}) failed: {e}")

    def trigger_build(self):
        """
        Trigger the build (by commenting/pushing to the PR/opening a new PR).
        :return:
        """
        logging.info(
            "Triggering a build for %s",
            self.pr if self.pr else "new PR",
        )
        if self.trigger == Trigger.comment:
            comment = self.comment or self.deployment.pr_comment
            self.pr.comment(comment)
        elif self.trigger == Trigger.push:
            self.push_to_pr()
        else:
            self.create_pr()

    def push_to_pr(self):
        """
        Push a new commit to the PR.
        :return:
        """
        branch = self.pr.source_branch
        commit_msg = f"Commit build trigger ({datetime.now(tz=timezone.utc).strftime('%d/%m/%y')})"
        self.head_commit = self.create_empty_commit(branch, commit_msg)

    def create_pr(self):
        """
        Create a new PR, if the source branch 'test_case_opened_pr' does not exist,
        create one and commit some changes before it.
        :return:
        """
        source_branch = f"test/{self.deployment.name}/opened_pr"
        pr_title = f"Basic test case ({self.deployment.name}): opened PR trigger"
        self.delete_previous_branch(source_branch)
        # Delete the PR from the previous test run if it exists.
        existing_pr = [pr for pr in self.project.get_pr_list() if pr.title == pr_title]
        if len(existing_pr) == 1:
            existing_pr[0].close()

        self.create_file_in_new_branch(source_branch)
        if self.deployment.opened_pr_trigger__packit_yaml_fix:
            self.fix_packit_yaml(source_branch)

        self.pr = self.project.create_pr(
            title=pr_title,
            body="This test case is triggered automatically by our validation script.",
            target_branch=self.project.default_branch,
            source_branch=source_branch,
        )
        self.head_commit = self.pr.head_commit

    def run_checks(self):
        """
        Run all checks of the test case.
        :return:
        """
        build = self.check_build_submitted()

        if not build:
            return

        self.check_build(build.id)
        self.check_completed_statuses()
        self.check_comment()

    def check_pending_check_runs(self):
        """
        Check whether some check run is set to queued
        (they are updated in loop, so it is enough).
        :return:
        """
        status_names = [self.get_status_name(status) for status in self.get_statuses()]

        watch_end = datetime.now(tz=timezone.utc) + timedelta(seconds=60)
        failure_message = "Github check runs were not set to queued in time 1 minute.\n"

        # when a new PR is opened
        while len(status_names) == 0:
            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += failure_message
                return
            status_names = [self.get_status_name(status) for status in self.get_statuses()]

        logging.info(
            "Watching pending statuses for commit %s",
            self.head_commit,
        )
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

            time.sleep(5)

    def check_build_submitted(self):
        """
        Check whether the build was submitted in Copr in time 15 minutes.
        :return:
        """
        if self.pr:
            try:
                old_build_len = len(
                    copr().build_proxy.get_list(self.deployment.copr_user, self.copr_project_name),
                )
            except Exception:
                old_build_len = 0

            old_comment_len = len(self.pr.get_comments())
        else:
            # the PR is not created yet
            old_build_len = 0
            old_comment_len = 0

        self.trigger_build()

        watch_end = datetime.now(tz=timezone.utc) + timedelta(seconds=60 * 15)

        self.check_pending_check_runs()

        logging.info(
            "Watching whether a build has been submitted for %s in %s",
            self.pr,
            self.copr_project_name,
        )
        while True:
            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += "The build was not submitted in Copr in time 15 minutes.\n"
                return None

            try:
                new_builds = copr().build_proxy.get_list(
                    self.deployment.copr_user,
                    self.copr_project_name,
                )
            except Exception as e:
                # project does not exist yet
                logging.warning(f"Copr project doesn't exist yet: {e}")
                continue

            if len(new_builds) >= old_build_len + 1:
                return new_builds[0]

            new_comments = self.pr.get_comments(reverse=True)
            new_comments = new_comments[: (len(new_comments) - old_comment_len)]

            if len(new_comments) > 1:
                comment = [
                    comment.body for comment in new_comments if comment.author == self.account_name
                ]
                if len(comment) > 0:
                    self.failure_msg += (
                        f"New github comment from p-s while submitting Copr build: {comment[0]}\n"
                    )

            time.sleep(30)

    def check_build(self, build_id):
        """
        Check whether the build was successful in Copr in time 15 minutes.
        :param build_id: ID of the build
        :return:
        """
        watch_end = datetime.now(tz=timezone.utc) + timedelta(seconds=60 * 15)
        state_reported = ""
        logging.info("Watching Copr build %s", build_id)

        while True:
            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += "The build did not finish in time 15 minutes.\n"
                return

            build = copr().build_proxy.get(build_id)
            if build.state == state_reported:
                time.sleep(20)
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

            time.sleep(30)

    def check_comment(self):
        """
        Check whether p-s has commented when the Copr build was not successful.
        :return:
        """
        failure = "The build in Copr was not successful." in self.failure_msg

        if failure:
            packit_comments = [
                comment
                for comment in self.pr.get_comments(reverse=True)
                if comment.author == self.account_name
            ]
            if not packit_comments:
                self.failure_msg += (
                    "No comment from p-s about unsuccessful last copr build found.\n"
                )

    def fix_packit_yaml(self, branch: str):
        """
        Update .packit.yaml file in the branch according to the deployment needs
        """
        path = ".packit.yaml"
        packit_yaml_content = self.project.get_file_content(path=path, ref=branch)
        packit_yaml_content = packit_yaml_content.replace(
            self.deployment.opened_pr_trigger__packit_yaml_fix.from_str,
            self.deployment.opened_pr_trigger__packit_yaml_fix.to_str,
        )

        self.update_file_and_commit(
            path=path,
            commit_msg=self.deployment.opened_pr_trigger__packit_yaml_fix.git_msg,
            content=packit_yaml_content,
            branch=branch,
        )

    def check_completed_statuses(self):
        """
        Check whether all check runs are set to success.
        :return:
        """
        if "The build in Copr was not successful." in self.failure_msg:
            return

        statuses = self.watch_statuses()
        for status in statuses:
            if not self.is_status_successful(status):
                self.failure_msg += (
                    f"Check run {self.get_status_name(status)} was set to failure.\n"
                )

    def watch_statuses(self):
        """
        Watch the check runs 20 minutes, if all the check runs have completed
        status, return the check runs.
        :return: list[CheckRun]
        """
        watch_end = datetime.now(tz=timezone.utc) + timedelta(seconds=60 * 20)
        logging.info(
            "Watching statuses for commit %s",
            self.head_commit,
        )

        while True:
            statuses = self.get_statuses()

            if all(self.is_status_completed(status) for status in statuses):
                break

            if datetime.now(tz=timezone.utc) > watch_end:
                self.failure_msg += (
                    "These check runs were not completed 20 minutes"
                    " after Copr build had been built:\n"
                )
                for status in statuses:
                    if not self.is_status_completed(status):
                        self.failure_msg += f"{self.get_status_name(status)}\n"
                return []

            time.sleep(20)

        return statuses

    @property
    def account_name(self):
        """
        Get the name of the (bot) account in GitHub/GitLab.
        """
        return

    def get_statuses(self) -> Union[list[GithubCheckRun], list[CommitFlag]]:
        """
        Get the statuses (checks in GitHub).
        """

    def is_status_completed(self, status: Union[GithubCheckRun, CommitFlag]) -> bool:
        """
        Check whether the status is in completed state (e.g. success, failure).
        """

    def is_status_successful(self, status: Union[GithubCheckRun, CommitFlag]) -> bool:
        """
        Check whether the status is in successful state.
        """

    def delete_previous_branch(self, ref: str):
        """
        Delete the branch from the previous test run if it exists.
        """

    def create_file_in_new_branch(self, branch: str):
        """
        Create a new branch and a new file in it via API (creates new commit).
        """

    def update_file_and_commit(self, path: str, commit_msg: str, content: str, branch: str):
        """
        Update a file via API (creates new commit).
        """

    def construct_copr_project_name(self) -> str:
        """
        Construct the Copr project name for the PR to check.
        """

    def get_status_name(self, status: Union[GithubCheckRun, CommitFlag]) -> str:
        """
        Get the name of the status/check that is visible to user.
        """

    def create_empty_commit(self, branch: str, commit_msg: str) -> str:
        """
        Create an empty commit via API.
        """
