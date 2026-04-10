# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import asyncio
import logging

from ogr.abstract import GitProject

from validation.deployment import DEPLOYMENT
from validation.testcase.base import TestFailureError
from validation.utils.trigger import Trigger


class Tests:
    project: GitProject
    test_case_kls: type
    # Minimum required API rate limit - can be overridden in subclasses
    min_required_rate_limit: int = 100
    # Stagger delay in seconds between tests - can be overridden in subclasses
    test_stagger_seconds: int = 0
    # Threshold for displaying delay in minutes vs seconds
    SECONDS_PER_MINUTE: int = 60

    async def check_rate_limit(self) -> None:
        """
        Check API rate limit before running tests.
        If quota is insufficient, wait proportionally and retry.
        """
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # Use OGR's built-in rate limit checking
                remaining = self.project.service.get_rate_limit_remaining()

                if remaining is None:
                    # Rate limit info not available (e.g., Pagure), skip the check
                    logging.debug(
                        "Rate limit information not available for %s, skipping check",
                        self.project.service.instance_url,
                    )
                    return

                logging.info(
                    "API rate limit for %s: %d requests remaining",
                    self.project.service.instance_url,
                    remaining,
                )

                if remaining < self.min_required_rate_limit:
                    # Calculate deficit and wait proportionally
                    deficit = self.min_required_rate_limit - remaining
                    # Wait time: roughly 1 second per missing request, with a minimum of 60s
                    # and maximum of 3600s (1 hour)
                    wait_seconds = max(60, min(deficit, 3600))

                    retry_count += 1
                    if retry_count >= max_retries:
                        logging.warning(
                            "Insufficient API quota for %s after %d retries: "
                            "%d remaining (need %d). Proceeding anyway.",
                            self.project.service.instance_url,
                            max_retries,
                            remaining,
                            self.min_required_rate_limit,
                        )
                        return

                    from datetime import datetime, timedelta, timezone

                    resume_time = datetime.now(tz=timezone.utc) + timedelta(seconds=wait_seconds)
                    logging.warning(
                        "Insufficient API quota for %s: %d remaining (need %d). "
                        "Waiting %d seconds until %s (retry %d/%d)",
                        self.project.service.instance_url,
                        remaining,
                        self.min_required_rate_limit,
                        wait_seconds,
                        resume_time.strftime("%H:%M:%S UTC"),
                        retry_count,
                        max_retries,
                    )
                    await asyncio.sleep(wait_seconds)
                    logging.info("Retrying rate limit check...")
                    continue  # Retry the check

                # Sufficient quota, proceed
                return

            except Exception as e:
                # Log but don't fail on errors
                logging.warning(
                    "Could not check rate limit for %s: %s. Proceeding anyway.",
                    self.project.service.instance_url,
                    e,
                )
                return

    async def run(self):
        # Check rate limit before starting tests
        await self.check_rate_limit()
        logging.info("Starting validation tests for %s", self.project.service.instance_url)
        logging.debug("Fetching PR list from %s/%s", self.project.namespace, self.project.repo)
        tasks = []
        test_metadata = []  # Track test details for summary

        # Fetch PR list once and cache it
        all_prs = list(self.project.get_pr_list())

        # Run non-comment tests first (these don't trigger abuse detection)
        # 1. New PR test (creates PR via API, no comment)
        msg = (
            "Run testcase where the build is triggered by opening "
            f"a new PR {self.project.service.instance_url}"
        )
        logging.info(msg)
        try:
            tasks.append(
                self.test_case_kls(
                    project=self.project,
                    deployment=DEPLOYMENT,
                    existing_prs=all_prs,
                ).run_test(),
            )
            test_metadata.append(
                {
                    "type": "new_pr",
                    "pr_url": None,  # Will be created during test
                    "pr_title": "New PR test",
                    "trigger": "pr_opened",
                },
            )
        except Exception as e:
            logging.exception("Failed to create test task: %s", e)
            raise

        # 2. Push trigger test (pushes to PR, no comment)
        pr_for_push = [
            pr for pr in all_prs if pr.title.startswith(DEPLOYMENT.push_trigger_tests_prefix)
        ]
        logging.debug("Found %d push trigger PRs", len(pr_for_push))
        if pr_for_push:
            msg = (
                "Run testcase where the build is triggered by push "
                f"for {self.project.service.instance_url}"
            )
            logging.warning(msg)
            tasks.append(
                self.test_case_kls(
                    project=self.project,
                    pr=pr_for_push[0],
                    trigger=Trigger.push,
                    deployment=DEPLOYMENT,
                ).run_test(),
            )
            test_metadata.append(
                {
                    "type": "push",
                    "pr_url": pr_for_push[0].url,
                    "pr_title": pr_for_push[0].title,
                    "trigger": "push",
                },
            )
        else:
            msg = (
                "No testcase found where the build is triggered by push "
                f"for {self.project.service.instance_url}"
            )
            logging.warning(msg)

        # 3. Comment-based tests
        basic_prs = [pr for pr in all_prs if pr.title.startswith("Basic test case:")]

        # Combine all comment-based test PRs
        all_comment_prs = [(pr, None) for pr in basic_prs]

        logging.debug(
            "Found %d basic test case PRs",
            len(basic_prs),
        )

        if all_comment_prs:
            logging.info(
                "Running %d comment-based tests for %s",
                len(all_comment_prs),
                self.project.service.instance_url,
            )

            for pr, comment in all_comment_prs:
                tasks.append(
                    self.test_case_kls(
                        project=self.project,
                        pr=pr,
                        trigger=Trigger.comment,
                        deployment=DEPLOYMENT,
                        comment=comment,
                    ).run_test(),
                )
                test_metadata.append(
                    {
                        "type": "comment",
                        "pr_url": pr.url,
                        "pr_title": pr.title,
                        "trigger": "comment",
                    },
                )
        else:
            logging.warning(
                "No comment-based test PRs found for %s",
                self.project.service.instance_url,
            )

        logging.info(
            "Created %d test tasks for %s",
            len(tasks),
            self.project.service.instance_url,
        )

        # Run tests with staggered starts to avoid API rate limiting
        # Stagger delay is configurable per service (test_stagger_seconds)
        async def run_with_delay(task, delay):
            if delay > 0:
                if delay >= self.SECONDS_PER_MINUTE:
                    minutes = delay // self.SECONDS_PER_MINUTE
                    logging.info("Waiting %d minutes before starting next test...", minutes)
                else:
                    logging.info("Waiting %d seconds before starting next test...", delay)
            await asyncio.sleep(delay)
            return await task

        staggered_tasks = [
            run_with_delay(task, i * self.test_stagger_seconds) for i, task in enumerate(tasks)
        ]

        # Wait for all tasks to complete
        results = await asyncio.gather(*staggered_tasks, return_exceptions=True)

        # Count successful and failed tests
        passed = sum(1 for r in results if r is True)
        failed = sum(1 for r in results if r is False or isinstance(r, Exception))
        total = len(results)

        # Collect failed test details
        failed_tests = []
        for i, result in enumerate(results):
            if result is False or isinstance(result, Exception):
                metadata = test_metadata[i] if i < len(test_metadata) else {}
                pr_url = metadata.get("pr_url", "Unknown")
                pr_title = metadata.get("pr_title", "Unknown")
                trigger = metadata.get("trigger", "unknown")

                if isinstance(result, TestFailureError):
                    # TestFailureError contains the actual failure message
                    reason = str(result)
                elif isinstance(result, Exception):
                    reason = f"Exception: {result!s}"
                else:
                    reason = "Test returned False (check logs for details)"

                failed_tests.append(
                    {
                        "pr_url": pr_url,
                        "pr_title": pr_title,
                        "trigger": trigger,
                        "reason": reason,
                    },
                )

        # Log summary at ERROR level if there are failures, otherwise INFO level
        separator = "=" * 60
        log_level = logging.ERROR if failed > 0 else logging.INFO

        summary_lines = [
            separator,
            f"Test Summary for {self.project.service.instance_url}:",
            f"  Total:  {total}",
            f"  Passed: {passed}",
            f"  Failed: {failed}",
        ]

        # Add failed test details if there are any failures
        if failed_tests:
            summary_lines.append("")
            summary_lines.append("Failed Tests:")
            for idx, failed_test in enumerate(failed_tests, 1):
                summary_lines.append(f"  {idx}. {failed_test['pr_title']}")
                if failed_test["pr_url"]:
                    summary_lines.append(f"     URL: {failed_test['pr_url']}")
                summary_lines.append(f"     Trigger: {failed_test['trigger']}")
                summary_lines.append(f"     Reason: {failed_test['reason']}")

        summary_lines.append(separator)

        logging.log(log_level, "\n".join(summary_lines))

        # Log detailed exceptions separately
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                metadata = test_metadata[i] if i < len(test_metadata) else {}
                pr_info = metadata.get("pr_url", f"Task {i}")
                logging.error(
                    "Detailed traceback for %s:",
                    pr_info,
                    exc_info=result,
                )
