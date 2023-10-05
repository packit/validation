# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

from os import getenv

from ogr import GithubService

from validation.testcase.github import GithubTestcase
from validation.tests.base import Tests


class GithubTests(Tests):
    test_case_kls = GithubTestcase

    def __init__(self):
        github_service = GithubService(token=getenv("GITHUB_TOKEN"))
        self.project = github_service.get_project(repo="hello-world", namespace="packit")
