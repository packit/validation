# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from os import getenv

from ogr import GitlabService
from ogr.abstract import GitProject
from ogr.services.github import GithubService
from ogr.services.gitlab import GitlabProject

from validation.deployment import DEPLOYMENT
from validation.testcase.github import GithubTestcase
from validation.testcase.gitlab import GitlabTestcase
from validation.utils.trigger import Trigger

logging.basicConfig(level=logging.INFO)


class Tests:
    project: GitProject
    test_case_kls: type

    def run(self):
        logging.info("Run testcases where the build is triggered by a '/packit build' comment")
        prs_for_comment = [
            pr for pr in self.project.get_pr_list() if pr.title.startswith("Basic test case:")
        ]
        for pr in prs_for_comment:
            self.test_case_kls(
                project=self.project,
                pr=pr,
                trigger=Trigger.comment,
                deployment=DEPLOYMENT,
            ).run_test()

        logging.info("Run testcase where the build is triggered by push")
        pr_for_push = [
            pr
            for pr in self.project.get_pr_list()
            if pr.title.startswith(DEPLOYMENT.push_trigger_tests_prefix)
        ]
        if pr_for_push:
            self.test_case_kls(
                project=self.project,
                pr=pr_for_push[0],
                trigger=Trigger.push,
                deployment=DEPLOYMENT,
            ).run_test()

        logging.info("Run testcase where the build is triggered by opening a new PR")
        self.test_case_kls(project=self.project, deployment=DEPLOYMENT).run_test()


class GitlabTests(Tests):
    test_case_kls = GitlabTestcase

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


class GithubTests(Tests):
    test_case_kls = GithubTestcase

    def __init__(self):
        github_service = GithubService(token=getenv("GITHUB_TOKEN"))
        self.project = github_service.get_project(repo="hello-world", namespace="packit")


if __name__ == "__main__":
    if getenv("GITLAB_TOKEN"):
        logging.info("Running validation for GitLab.")
        GitlabTests().run()
    else:
        logging.info("GITLAB_TOKEN not set, skipping the validation for GitLab.")

    if getenv("GITLAB_GNOME_TOKEN"):
        logging.info("Running validation for GitLab (gitlab.gnome.org instance).")
        GitlabTests(
            instance_url="https://gitlab.gnome.org/",
            namespace="packit-validation",
            token_name="GITLAB_GNOME_TOKEN",
        ).run()
    else:
        logging.info(
            "GITLAB_GNOME_TOKEN not set, "
            "skipping the validation for GitLab (gitlab.gnome.org instance).",
        )

    if getenv("GITLAB_FREEDESKTOP_TOKEN"):
        logging.info("Running validation for GitLab (gitlab.freedesktop.org instance).")
        GitlabTests(
            instance_url="https://gitlab.freedesktop.org/",
            namespace="packit-service",
            token_name="GITLAB_FREEDESKTOP_TOKEN",
        ).run()
    else:
        logging.info(
            "GITLAB_FREEDESKTOP_TOKEN not set, "
            "skipping the validation for GitLab (gitlab.freedesktop.org instance).",
        )

    if getenv("SALSA_DEBIAN_TOKEN"):
        logging.info("Running validation for GitLab (salsa.debian.org instance).")
        GitlabTests(
            instance_url="https://salsa.debian.org/",
            namespace="packit-validation",
            token_name="SALSA_DEBIAN_TOKEN",
        ).run()
    else:
        logging.info(
            "SALSA_DEBIAN_TOKEN not set, "
            "skipping the validation for GitLab (salsa.debian.org instance).",
        )

    if getenv("GITHUB_TOKEN"):
        logging.info("Running validation for GitHub.")
        GithubTests().run()
    else:
        logging.info("GITHUB_TOKEN not set, skipping the validation for GitHub.")
