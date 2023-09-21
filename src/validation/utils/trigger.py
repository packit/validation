# SPDX-FileCopyrightText: 2023-present Contributors to the Packit Project.
#
# SPDX-License-Identifier: MIT

import enum


class Trigger(str, enum.Enum):
    comment = "comment"
    pr_opened = "pr_opened"
    push = "push"
