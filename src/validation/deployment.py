# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import enum
from dataclasses import dataclass
from os import getenv
from typing import Optional


# Everywhere else in the deployment repo environments are called 'prod' and 'stg'.
# Call them some other name here to avoid accidentally deploying the wrong thing.
class Deployment(str, enum.Enum):
    production = "production"
    staging = "staging"


@dataclass
class YamlFix:
    from_str: str = ""
    to_str: str = ""
    git_msg: str = ""


@dataclass
class DeploymentInfo:
    name: str
    app_name: str
    pr_comment: str
    pr_comment_vm_image_build: str
    opened_pr_trigger__packit_yaml_fix: Optional[YamlFix]
    copr_user: str
    push_trigger_tests_prefix: str
    github_bot_name: str
    gitlab_account_name: str


PRODUCTION_INFO = DeploymentInfo(
    name="prod",
    app_name="Packit-as-a-Service",
    pr_comment="/packit build",
    pr_comment_vm_image_build="/packit vm-image-build",
    opened_pr_trigger__packit_yaml_fix=None,
    copr_user="packit",
    push_trigger_tests_prefix="Basic test case (prod): push trigger",
    github_bot_name="packit-as-a-service[bot]",
    gitlab_account_name="packit-as-a-service",
)
STAGING_INFO = DeploymentInfo(
    name="stg",
    app_name="Packit-as-a-Service-stg",
    pr_comment="/packit-stg build",
    pr_comment_vm_image_build="/packit-stg vm-image-build",
    opened_pr_trigger__packit_yaml_fix=YamlFix(
        from_str="---",
        to_str='---\npackit_instances: ["stg"]',
        git_msg="Build using Packit-stg",
    ),
    copr_user="packit-stg",
    push_trigger_tests_prefix="Basic test case (stg): push trigger",
    github_bot_name="packit-as-a-service-stg[bot]",
    gitlab_account_name="packit-as-a-service-stg",
)

DEPLOYMENT = (
    PRODUCTION_INFO
    if getenv("DEPLOYMENT", Deployment.production) == Deployment.production
    else STAGING_INFO
)
