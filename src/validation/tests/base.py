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
        loop = asyncio.get_event_loop()
        prs_for_comment = [
            pr for pr in self.project.get_pr_list() if pr.title.startswith("Test VM Image builds")
        ]
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
        for pr in prs_for_comment:
            loop.create_task(
                self.test_case_kls(
                    project=self.project,
                    pr=pr,
                    trigger=Trigger.comment,
                    deployment=DEPLOYMENT,
                    comment=DEPLOYMENT.pr_comment_vm_image_build,
                ).run_test(),
            )

        prs_for_comment = [
            pr for pr in self.project.get_pr_list() if pr.title.startswith("Basic test case:")
        ]
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
        for pr in prs_for_comment:
            loop.create_task(
                self.test_case_kls(
                    project=self.project,
                    pr=pr,
                    trigger=Trigger.comment,
                    deployment=DEPLOYMENT,
                ).run_test(),
            )

        pr_for_push = [
            pr
            for pr in self.project.get_pr_list()
            if pr.title.startswith(DEPLOYMENT.push_trigger_tests_prefix)
        ]
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
            loop.create_task(
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
        loop.create_task(self.test_case_kls(project=self.project, deployment=DEPLOYMENT).run_test())
