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
