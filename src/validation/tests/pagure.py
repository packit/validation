# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import logging
from os import getenv

from ogr import PagureService
from ogr.services.pagure import PagureProject

from validation.helpers import KerberosError, destroy_kerberos_ticket, init_kerberos_ticket
from validation.testcase.pagure import PagureTestcase
from validation.tests.base import Tests


class PagureTests(Tests):
    test_case_kls = PagureTestcase

    def __init__(
        self,
        instance_url="https://src.fedoraproject.org/",
        namespace="rpms",
        token_name="PAGURE_TOKEN",
    ):
        pagure_service = PagureService(token=getenv(token_name), instance_url=instance_url)
        self.project: PagureProject = pagure_service.get_project(
            repo="python-requre",
            namespace=namespace,
        )
        self._kerberos_principal = None

    async def run(self):
        """Override run to initialize Kerberos ticket before tests."""
        keytab_file = getenv("PAGURE_KEYTAB")

        if keytab_file:
            try:
                self._kerberos_principal = await init_kerberos_ticket(keytab_file)
                logging.info("Kerberos ticket initialized for Pagure tests")
            except KerberosError as e:
                logging.error("Failed to initialize Kerberos ticket: %s", e)
                logging.warning("Continuing without Kerberos ticket - some operations may fail")

        try:
            # Run the actual tests
            await super().run()
        finally:
            # Clean up Kerberos ticket
            if self._kerberos_principal:
                try:
                    await destroy_kerberos_ticket(self._kerberos_principal)
                    logging.info("Kerberos ticket destroyed")
                except Exception as e:
                    logging.warning("Failed to destroy Kerberos ticket: %s", e)
