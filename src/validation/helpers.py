# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import asyncio
import logging
import re
import subprocess
from functools import lru_cache
from os import getenv

import koji as koji_module
from copr.v3 import Client


class KerberosError(Exception):
    """Exception raised for Kerberos-related errors."""


@lru_cache
def copr():
    return Client({"copr_url": "https://copr.fedorainfracloud.org"})


@lru_cache
def koji():
    """
    Create and return a Koji session for querying Fedora Koji builds.
    """
    koji_url = getenv("KOJI_URL", "https://koji.fedoraproject.org/kojihub")
    return koji_module.ClientSession(koji_url)


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


async def extract_principal_from_keytab(keytab_file: str) -> str:
    """
    Extract principal from the specified keytab file.
    Assumes there is a single principal in the keytab.

    Args:
        keytab_file: Path to a keytab file.

    Returns:
        Extracted principal name.
    """
    proc = await asyncio.create_subprocess_exec(
        "klist",
        "-k",
        "-K",
        "-e",
        keytab_file,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode:
        logging.error("klist command failed: %s", stderr.decode())
        msg = "klist command failed"
        raise KerberosError(msg)

    # Parse klist output to extract principal
    # Format: "   2 principal@REALM (aes256-cts-hmac-sha1-96) (0x...)"
    key_pattern = re.compile(r"^\s*(\d+)\s+(\S+)\s+\((\S+)\)\s+\((\S+)\)$")
    for line in stdout.decode().splitlines():
        if match := key_pattern.match(line):
            # Return the principal associated with the first key
            return match.group(2)

    msg = "No valid key found in the keytab file"
    raise KerberosError(msg)


async def init_kerberos_ticket(keytab_file: str) -> str:
    """
    Initialize Kerberos ticket from keytab file.

    Args:
        keytab_file: Path to keytab file

    Returns:
        Principal name for which ticket was initialized
    """
    # Extract principal from keytab
    principal = await extract_principal_from_keytab(keytab_file)
    logging.debug("Extracted principal from keytab: %s", principal)

    # Check if ticket already exists
    proc = await asyncio.create_subprocess_exec(
        "klist",
        "-l",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        # Parse existing principals
        principals = [
            parts[0]
            for line in stdout.decode().splitlines()
            if "Expired" not in line
            for parts in (line.split(),)
            if len(parts) >= 1 and "@" in parts[0]
        ]

        if principal in principals:
            logging.info("Using existing Kerberos ticket for %s", principal)
            return principal

    # Initialize new ticket
    logging.info("Initializing Kerberos ticket for %s", principal)
    proc = await asyncio.create_subprocess_exec(
        "kinit",
        "-k",
        "-t",
        keytab_file,
        principal,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode:
        logging.error("kinit failed: %s", stderr.decode())
        msg = "kinit command failed"
        raise KerberosError(msg)

    logging.info("Kerberos ticket initialized for %s", principal)
    return principal


async def destroy_kerberos_ticket(principal: str):
    """
    Destroy Kerberos ticket for the specified principal.

    Args:
        principal: Principal name whose ticket should be destroyed
    """
    logging.info("Destroying Kerberos ticket for %s", principal)
    proc = await asyncio.create_subprocess_exec(
        "kdestroy",
        "-p",
        principal,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    await proc.communicate()

    if proc.returncode:
        logging.warning("Failed to destroy Kerberos ticket for %s", principal)
