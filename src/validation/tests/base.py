# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import logging

from ogr.abstract import GitProject

from validation.deployment import DEPLOYMENT
from validation.utils.trigger import Trigger


class Tests:
    project: GitProject
    test_case_kls: type

    def run(self):
        prs_for_comment = [
            pr for pr in self.project.get_pr_list() if pr.title.startswith("Test VM Image builds")
        ]
        if prs_for_comment:
            logging.info("Run testcases where the build is triggered by a ‹vm-image-build› comment")
        else:
            logging.warning(
                "No testcases found where the build is triggered by a ‹vm-image-build› comment",
            )
        for pr in prs_for_comment:
            self.test_case_kls(
                project=self.project,
                pr=pr,
                trigger=Trigger.comment,
                deployment=DEPLOYMENT,
                comment=DEPLOYMENT.pr_comment_vm_image_build,
            ).run_test()

        prs_for_comment = [
            pr for pr in self.project.get_pr_list() if pr.title.startswith("Basic test case:")
        ]
        if prs_for_comment:
            logging.info("Run testcases where the build is triggered by a ‹build› comment")
        else:
            logging.warning("No testcases found where the build is triggered by a ‹build› comment")
        for pr in prs_for_comment:
            self.test_case_kls(
                project=self.project,
                pr=pr,
                trigger=Trigger.comment,
                deployment=DEPLOYMENT,
            ).run_test()

        pr_for_push = [
            pr
            for pr in self.project.get_pr_list()
            if pr.title.startswith(DEPLOYMENT.push_trigger_tests_prefix)
        ]
        if pr_for_push:
            logging.info("Run testcase where the build is triggered by push")
        else:
            logging.warning("No testcase found where the build is triggered by push")
        if pr_for_push:
            self.test_case_kls(
                project=self.project,
                pr=pr_for_push[0],
                trigger=Trigger.push,
                deployment=DEPLOYMENT,
            ).run_test()

        logging.info("Run testcase where the build is triggered by opening a new PR")
        self.test_case_kls(project=self.project, deployment=DEPLOYMENT).run_test()
