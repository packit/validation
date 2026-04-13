# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from gitlab import GitlabGetError
from ogr.abstract import CommitFlag, CommitStatus
from ogr.services.gitlab import GitlabProject

from validation.testcase.base import Testcase
from validation.utils.trigger import Trigger


class GitlabTestcase(Testcase):
    project: GitlabProject

    @property
    def account_name(self):
        return self.deployment.gitlab_account_name

    def construct_copr_project_name(self) -> str:
        return f"{self.project.service.hostname}-{self.project.namespace}-hello-world-{self.pr.id}"

    def get_status_name(self, status: CommitFlag) -> str:
        return status.context

    def create_file_in_new_branch(self, branch: str):
        self.pr_branch_ref = self.project.gitlab_repo.branches.create(
            {"branch": branch, "ref": "master"},
        )

        self.project.gitlab_repo.files.create(
            {
                "file_path": "test.txt",
                "branch": branch,
                "content": "Testing the opened PR trigger.",
                "author_email": "validation@packit.dev",
                "author_name": "Packit Validation",
                "commit_message": "Opened PR trigger",
            },
        )

    def _check_status_author(self, status: CommitFlag) -> bool:
        """
        Check if status author matches the account name.
        Returns True if match, False otherwise (including on errors).
        """
        try:
            if not status._raw_commit_flag or not status._raw_commit_flag.author:
                return False
            author_username = status._raw_commit_flag.author["username"]
            logging.debug(
                "Status '%s' by '%s' - Match: %s",
                status.context,
                author_username,
                author_username == self.account_name,
            )
            return author_username == self.account_name
        except (KeyError, AttributeError, TypeError) as e:
            logging.warning(
                "Failed to get author for status %s: %s - Raw: %s",
                status.context,
                e,
                status._raw_commit_flag,
            )
            return False

    def get_statuses(self) -> list[CommitFlag]:
        all_statuses = list(self.project.get_commit_statuses(commit=self.head_commit))

        logging.debug(
            "Fetching statuses for commit %s, looking for author: %s",
            self.head_commit,
            self.account_name,
        )

        filtered_statuses = [status for status in all_statuses if self._check_status_author(status)]

        logging.debug(
            "Found %d/%d statuses from %s",
            len(filtered_statuses),
            len(all_statuses),
            self.account_name,
        )

        return filtered_statuses

    def is_status_successful(self, status: CommitFlag) -> bool:
        return status.state == CommitStatus.success

    def is_status_completed(self, status: CommitFlag) -> bool:
        return status.state not in [
            CommitStatus.running,
            CommitStatus.pending,
        ]

    def is_status_recent(self, status: CommitFlag) -> bool:
        """
        Check if the status was created after the build was triggered.
        Uses created timestamp with a 1-minute buffer for clock skew.
        """
        if not self._build_triggered_at:
            return True  # No trigger time set, accept all statuses
        if not status.created:
            return True  # No timestamp on status, accept it

        # Convert naive datetime to UTC-aware if needed
        status_time = self._ensure_aware_datetime(status.created)

        # Allow 1 minute buffer for clock skew
        buffer_time = self._build_triggered_at - timedelta(minutes=1)
        return status_time >= buffer_time

    def delete_previous_branch(self, branch: str):
        try:
            existing_branch = self.project.gitlab_repo.branches.get(branch)
        except GitlabGetError:
            return

        existing_branch.delete()

    def update_file_and_commit(self, path: str, commit_msg: str, content: str, branch: str):
        file = self.project.gitlab_repo.files.get(file_path=path, ref=branch)
        file.content = content
        file.save(branch=branch, commit_message=commit_msg)

    def create_empty_commit(self, branch: str, commit_msg: str) -> str:
        data = {
            "branch": branch,
            "commit_message": commit_msg,
            "actions": [],
            "allow_empty": True,
        }
        commit = self.project.gitlab_repo.commits.create(data)
        return commit.id

    async def check_pending_check_runs(self):
        """
        Override to add extended wait time for GitLab opened PR webhook delays.
        GitLab webhook delivery can be delayed by over an hour during high load.
        """
        # First, try the normal check with standard timeouts
        initial_failure_msg = self.failure_msg
        await super().check_pending_check_runs()

        # If this is an opened PR trigger and the initial check failed
        if (
            self.trigger == Trigger.pr_opened
            and self.failure_msg != initial_failure_msg
            and "Commit statuses did not appear in time" in self.failure_msg
        ):
            logging.error(
                "GitLab webhook delivery delayed - statuses did not appear within %d minutes. "
                "This is a known issue with GitLab webhook queuing during high load. "
                "Waiting an additional 60 minutes for delayed webhook delivery...",
                self.CHECK_TIME_FOR_STATUSES_TO_APPEAR,
            )

            # Clear the failure message and wait longer
            self.failure_msg = initial_failure_msg

            # Wait up to 60 more minutes for statuses to appear
            watch_end = datetime.now(tz=timezone.utc) + timedelta(minutes=60)
            all_statuses = self.get_statuses()
            status_names = [self.get_status_name(status) for status in all_statuses]

            while len(status_names) == 0:
                if datetime.now(tz=timezone.utc) > watch_end:
                    logging.error(
                        "GitLab webhook still not received after extended 60 minute wait. "
                        "Total wait time: %d minutes. "
                        "This indicates a significant GitLab webhook delay.",
                        self.CHECK_TIME_FOR_STATUSES_TO_APPEAR + 60,
                    )
                    self.failure_msg += (
                        f"Commit statuses did not appear even after extended wait "
                        f"({self.CHECK_TIME_FOR_STATUSES_TO_APPEAR + 60} minutes total).\n"
                        "Note: GitLab webhook delivery was significantly delayed.\n"
                    )
                    return
                await asyncio.sleep(30)
                all_statuses = self.get_statuses()
                status_names = [self.get_status_name(status) for status in all_statuses]

            logging.error(
                "GitLab webhook eventually received after extended wait. "
                "Statuses appeared, continuing with test validation. "
                "This delay is expected for GitLab during high load periods.",
            )

            # Continue with phase 2: wait for statuses to be set to pending
            logging.info(
                "Watching pending statuses for commit %s",
                self.head_commit,
            )

            await asyncio.sleep(5)

            watch_end = datetime.now(tz=timezone.utc) + timedelta(
                minutes=self.CHECK_TIME_FOR_REACTION,
            )

            while True:
                if datetime.now(tz=timezone.utc) > watch_end:
                    self.failure_msg += (
                        f"Commit statuses were not set to pending in time "
                        f"({self.CHECK_TIME_FOR_REACTION} minutes).\n"
                    )
                    return

                new_statuses = [
                    status
                    for status in self.get_statuses()
                    if self.get_status_name(status) in status_names
                ]

                for status in new_statuses:
                    if not self.is_status_completed(status):
                        logging.info(
                            "At least one commit status is now pending/running: %s",
                            self.get_status_name(status),
                        )
                        return

                await asyncio.sleep(30)
