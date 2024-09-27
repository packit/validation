# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

from os import getenv

from ogr import GitlabService
from ogr.services.gitlab import GitlabProject

from validation.testcase.gitlab import GitlabTestcase
from validation.tests.base import Tests


class GitlabTests(Tests):
    test_case_kls = GitlabTestcase

    def __init__(
        self,
        instance_url="https://gitlab.com",
        namespace="packit",
        token_name="GITLAB_TOKEN",
    ):
        gitlab_service = GitlabService(token=getenv(token_name), instance_url=instance_url)
        self.project: GitlabProject = gitlab_service.get_project(
            repo="hello-world",
            namespace=namespace,
        )
