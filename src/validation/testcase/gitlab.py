# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import logging
from datetime import timedelta

from gitlab import GitlabGetError
from ogr.abstract import CommitFlag, CommitStatus
from ogr.services.gitlab import GitlabProject

from validation.testcase.base import Testcase


class GitlabTestcase(Testcase):
    # Gitlab instances are more slow than GitHub
    CHECK_TIME_FOR_STATUSES_TO_APPEAR = (
        3  # minutes - time to wait for statuses to appear after trigger
    )
    CHECK_TIME_FOR_REACTION = 3  # minutes - time to wait for commit statuses to be set to pending
    CHECK_TIME_FOR_SUBMIT_BUILDS = 7  # minutes - time to wait for build to be submitted in Copr
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
