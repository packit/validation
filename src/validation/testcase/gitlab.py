# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

from gitlab import GitlabGetError
from ogr.abstract import CommitFlag, CommitStatus
from ogr.services.gitlab import GitlabProject

from validation.testcase.base import Testcase


class GitlabTestcase(Testcase):
    project: GitlabProject

    @property
    def account_name(self):
        return self.deployment.gitlab_account_name

    def get_status_name(self, status: CommitFlag) -> str:
        return status.context

    def construct_copr_project_name(self) -> str:
        return f"{self.project.service.hostname}-{self.project.namespace}-hello-world-{self.pr.id}"

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

    def get_statuses(self) -> list[CommitFlag]:
        return [
            status
            for status in self.project.get_commit_statuses(commit=self.head_commit)
            if status._raw_commit_flag.author["username"] == self.account_name
        ]

    def is_status_successful(self, status: CommitFlag) -> bool:
        return status.state == CommitStatus.success

    def is_status_completed(self, status: CommitFlag) -> bool:
        return status.state not in [
            CommitStatus.running,
            CommitStatus.pending,
        ]

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
        data = {"branch": branch, "commit_message": commit_msg, "actions": []}
        commit = self.project.gitlab_repo.commits.create(data)
        return commit.id
