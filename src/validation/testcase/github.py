# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

from datetime import timedelta

from github import InputGitAuthor
from github.Commit import Commit
from ogr.services.github import GithubProject
from ogr.services.github.check_run import (
    GithubCheckRun,
    GithubCheckRunResult,
    GithubCheckRunStatus,
)

from validation.testcase.base import Testcase


class GithubTestcase(Testcase):
    project: GithubProject
    user = InputGitAuthor(name="Release Bot", email="user-cont-team+release-bot@redhat.com")

    @property
    def account_name(self):
        return self.deployment.github_bot_name

    def construct_copr_project_name(self) -> str:
        return f"packit-hello-world-{self.pr.id}"

    def get_status_name(self, status: GithubCheckRun) -> str:
        return status.name

    def create_empty_commit(self, branch: str, commit_msg: str) -> str:
        contents = self.project.github_repo.get_contents("test.txt", ref=branch)
        # https://pygithub.readthedocs.io/en/latest/examples/Repository.html#update-a-file-in-the-repository
        # allows empty commit (always the same content of file)
        commit: Commit = self.project.github_repo.update_file(
            path=contents.path,
            message=commit_msg,
            content="Testing the push trigger.",
            sha=contents.sha,
            branch=branch,
            committer=self.user,
            author=self.user,
        )["commit"]
        return commit.sha

    def get_statuses(self) -> list[GithubCheckRun]:
        return [
            check_run
            for check_run in self.project.get_check_runs(commit_sha=self.head_commit)
            if check_run.app.name == self.deployment.app_name
        ]

    def is_status_successful(self, status: GithubCheckRun) -> bool:
        return status.conclusion == GithubCheckRunResult.success

    def is_status_completed(self, status: GithubCheckRun) -> bool:
        return status.status == GithubCheckRunStatus.completed

    def is_status_recent(self, status: GithubCheckRun) -> bool:
        """
        Check if the status was created after the build was triggered.
        Uses started_at timestamp with a 1-minute buffer for clock skew.
        """
        if not self._build_triggered_at:
            return True  # No trigger time set, accept all statuses
        if not status.started_at:
            return True  # No timestamp on status, accept it

        # Convert naive datetime to UTC-aware if needed
        status_time = self._ensure_aware_datetime(status.started_at)

        # Allow 1 minute buffer for clock skew
        buffer_time = self._build_triggered_at - timedelta(minutes=1)
        return status_time >= buffer_time

    def delete_previous_branch(self, branch: str):
        existing_branch = self.project.github_repo.get_git_matching_refs(f"heads/{branch}")
        if existing_branch.totalCount:
            existing_branch[0].delete()

    def create_file_in_new_branch(self, branch: str):
        commit = self.project.github_repo.get_commit("HEAD")
        ref = f"refs/heads/{branch}"
        self.pr_branch_ref = self.project.github_repo.create_git_ref(ref, commit.sha)
        self.project.github_repo.create_file(
            path="test.txt",
            message="Opened PR trigger",
            content="Testing the opened PR trigger.",
            branch=branch,
            committer=self.user,
            author=self.user,
        )

    def update_file_and_commit(self, path: str, commit_msg: str, content: str, branch: str):
        contents = self.project.github_repo.get_contents(path=path, ref=branch)
        self.project.github_repo.update_file(
            path,
            commit_msg,
            content,
            contents.sha,
            branch=branch,
            committer=self.user,
            author=self.user,
        )
