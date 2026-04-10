# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import asyncio
import logging
from os import getenv

import click

from validation.tests.github import GithubTests
from validation.tests.gitlab import GitlabTests
from validation.tests.pagure import PagureTests

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, invoke_without_command=True)
@click.version_option(prog_name="validation")
def validation():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    tasks = []

    # GitHub
    if getenv("GITHUB_TOKEN"):
        logging.info("Running validation for GitHub.")
        tasks.append(GithubTests().run())
    else:
        logging.info("GITHUB_TOKEN not set, skipping the validation for GitHub.")

    # GitLab
    gitlab_instances = [
        ("https://gitlab.com", "packit-service", "GITLAB_TOKEN"),
        ("https://gitlab.gnome.org", "packit-validation", "GITLAB_GNOME_TOKEN"),
        (
            "https://gitlab.freedesktop.org",
            "packit-service",
            "GITLAB_FREEDESKTOP_TOKEN",
        ),
        ("https://salsa.debian.org", "packit-validation", "SALSA_DEBIAN_TOKEN"),
    ]
    for instance_url, namespace, token in gitlab_instances:
        if not getenv(token):
            logging.info(
                "%s not set, skipping the validation for GitLab instance: %s",
                token,
                instance_url,
            )
            continue

        logging.info("Running validation for GitLab instance: %s", instance_url)
        tasks.append(
            GitlabTests(
                instance_url=instance_url,
                namespace=namespace,
                token_name=token,
            ).run(),
        )

    # Pagure
    pagure_instances = [
        ("https://src.fedoraproject.org/", "rpms", "PAGURE_TOKEN"),
    ]
    for instance_url, namespace, token in pagure_instances:
        if not getenv(token):
            logging.info(
                "%s not set, skipping the validation for Pagure instance: %s",
                token,
                instance_url,
            )
            continue

        logging.info("Running validation for Pagure instance: %s", instance_url)
        tasks.append(
            PagureTests(
                instance_url=instance_url,
                namespace=namespace,
                token_name=token,
            ).run(),
        )

    if not tasks:
        logging.error("No tokens configured, no validation tests to run")
        raise SystemExit(1)

    logging.info("Running %d validation test suite(s)", len(tasks))
    try:
        results = loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        logging.info("All validation tests completed")

        # Check if any test suite failed
        failed_count = sum(1 for result in results if isinstance(result, Exception))
        if failed_count:
            logging.error("%d test suite(s) failed", failed_count)
            raise SystemExit(1)
    except KeyboardInterrupt:
        logging.info("Validation interrupted by user")
        raise SystemExit(130) from None
    finally:
        loop.close()
