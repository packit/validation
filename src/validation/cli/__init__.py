# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import asyncio
import logging
from os import getenv

import click

from validation.tests.github import GithubTests
from validation.tests.gitlab import GitlabTests

logging.basicConfig(level=logging.INFO)


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, invoke_without_command=True)
@click.version_option(prog_name="validation")
def validation():
    loop = asyncio.get_event_loop()
    # GitHub
    if getenv("GITHUB_TOKEN"):
        logging.info("Running validation for GitHub.")
        loop.create_task(GithubTests().run())
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
        loop.create_task(
            GitlabTests(
                instance_url=instance_url,
                namespace=namespace,
                token_name=token,
            ).run(),
        )

    loop.run_forever()
