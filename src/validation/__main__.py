# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import sys

if __name__ == "__main__":
    from validation.cli import validation

    sys.exit(validation())
