# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import asyncio
import logging

from ogr.abstract import GitProject

from validation.deployment import DEPLOYMENT
from validation.utils.trigger import Trigger


class Tests:
    project: GitProject
    test_case_kls: type

    async def run(self):
        logging.info("Starting validation tests for %s", self.project.service.instance_url)
        logging.debug("Fetching PR list from %s/%s", self.project.namespace, self.project.repo)
        tasks = []

        prs_for_comment = [
            pr for pr in self.project.get_pr_list() if pr.title.startswith("Test VM Image builds")
        ]
        logging.debug("Found %d VM image build PRs", len(prs_for_comment))
        if prs_for_comment:
            msg = (
                "Run testcases where the build is triggered by a "
                f"‹vm-image-build› comment for {self.project.service.instance_url}"
            )
        else:
            msg = (
                "No testcases found where the build is triggered by a "
                f"‹vm-image-build› comment for {self.project.service.instance_url}"
            )
        logging.warning(msg)
        tasks.extend(
            [
                self.test_case_kls(
                    project=self.project,
                    pr=pr,
                    trigger=Trigger.comment,
                    deployment=DEPLOYMENT,
                    comment=DEPLOYMENT.pr_comment_vm_image_build,
                ).run_test()
                for pr in prs_for_comment
            ],
        )

        prs_for_comment = [
            pr for pr in self.project.get_pr_list() if pr.title.startswith("Basic test case:")
        ]
        logging.debug("Found %d basic test case PRs", len(prs_for_comment))
        if prs_for_comment:
            msg = (
                "Run testcases where the build is triggered by a "
                f"‹build› comment for {self.project.service.instance_url}"
            )
        else:
            msg = (
                "No testcases found where the build is triggered by a "
                f"‹build› comment for {self.project.service.instance_url}"
            )
        logging.warning(msg)
        tasks.extend(
            [
                self.test_case_kls(
                    project=self.project,
                    pr=pr,
                    trigger=Trigger.comment,
                    deployment=DEPLOYMENT,
                ).run_test()
                for pr in prs_for_comment
            ],
        )

        pr_for_push = [
            pr
            for pr in self.project.get_pr_list()
            if pr.title.startswith(DEPLOYMENT.push_trigger_tests_prefix)
        ]
        logging.debug("Found %d push trigger PRs", len(pr_for_push))
        if pr_for_push:
            msg = (
                "Run testcase where the build is triggered by push "
                f"for {self.project.service.instance_url}"
            )
        else:
            msg = (
                "No testcase found where the build is triggered by push "
                f"for {self.project.service.instance_url}"
            )
        logging.warning(msg)
        if pr_for_push:
            tasks.append(
                self.test_case_kls(
                    project=self.project,
                    pr=pr_for_push[0],
                    trigger=Trigger.push,
                    deployment=DEPLOYMENT,
                ).run_test(),
            )

        msg = (
            "Run testcase where the build is triggered by opening "
            f"a new PR {self.project.service.instance_url}"
        )
        logging.info(msg)
        try:
            tasks.append(self.test_case_kls(project=self.project, deployment=DEPLOYMENT).run_test())
            logging.info(
                "Created %d test tasks for %s",
                len(tasks),
                self.project.service.instance_url,
            )
        except Exception as e:
            logging.exception("Failed to create test task: %s", e)
            raise

        # Wait for all tasks to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logging.info("All test tasks completed for %s", self.project.service.instance_url)

        # Log any exceptions that occurred
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logging.error("Task %d failed with exception: %s", i, result, exc_info=result)
