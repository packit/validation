# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import enum
from dataclasses import dataclass
from os import getenv
from typing import Union


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
class ProductionInfo:
    name: str = "prod"
    app_name: str = "Packit-as-a-Service"
    pr_comment: str = "/packit build"
    opened_pr_trigger__packit_yaml_fix: YamlFix = None
    copr_user = "packit"
    push_trigger_tests_prefix = "Basic test case - push trigger"
    github_bot_name = "packit-as-a-service[bot]"
    gitlab_account_name = "packit-as-a-service"


@dataclass
class StagingInfo:
    name: str = "stg"
    app_name = "Packit-as-a-Service-stg"
    pr_comment = "/packit-stg build"
    opened_pr_trigger__packit_yaml_fix = YamlFix(
        "---",
        '---\npackit_instances: ["stg"]',
        "Build using Packit-stg",
    )
    copr_user = "packit-stg"
    push_trigger_tests_prefix = "Basic test case (stg) - push trigger"
    github_bot_name = "packit-as-a-service-stg[bot]"
    gitlab_account_name = "packit-as-a-service-stg"


DeploymentInfo = Union[ProductionInfo, StagingInfo]

DEPLOYMENT = (
    ProductionInfo()
    if getenv("DEPLOYMENT", Deployment.production) == Deployment.production
    else StagingInfo()
)
