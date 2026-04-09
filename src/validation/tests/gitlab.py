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
    # We need at least 50 requests per test, and we run multiple tests
    # (new PR, push trigger, comment tests)
    # GitLab has more generous limits than GitHub (2000/min vs 5000/hour)
    min_required_rate_limit = 250
    # Stagger tests by 60 seconds to avoid race conditions in packit-service
    # when multiple events arrive simultaneously for the same project
    test_stagger_seconds = 60

    def __init__(
        self,
        instance_url="https://gitlab.com",
        namespace="packit-service",
        token_name="GITLAB_TOKEN",
    ):
        gitlab_service = GitlabService(token=getenv(token_name), instance_url=instance_url)
        self.project: GitlabProject = gitlab_service.get_project(
            repo="hello-world",
            namespace=namespace,
        )
