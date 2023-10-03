# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
from functools import lru_cache
from os import getenv

from copr.v3 import Client


@lru_cache
def copr():
    return Client({"copr_url": "https://copr.fedorainfracloud.org"})


@lru_cache
def sentry_sdk():
    if sentry_secret := getenv("SENTRY_SECRET"):
        import sentry_sdk

        sentry_sdk.init(sentry_secret)
        return sentry_sdk

    logging.warning("SENTRY_SECRET was not set!")
    return None


def log_failure(message: str):
    if sdk := sentry_sdk():
        sdk.capture_message(message)
        return

    logging.warning(message)
