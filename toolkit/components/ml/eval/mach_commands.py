# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import getpass
import os

from mach.decorators import Command, CommandArgument, SubCommand
from mozbuild.base import MachCommandBase


class EvalCommand(MachCommandBase):
    """Forward eval runs to mozperftest and mochitests."""

    @Command(
        "eval",
        category="testing",
        description="Run evaluation tests, backed by perftests and mochitests",
    )
    @CommandArgument(
        "paths",
        nargs="*",
        help="The eval test path located in browser_eval directories",
    )
    @CommandArgument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional mochitest arguments passed through to perftest.",
    )
    def run_eval(self, paths: list[str], extra_args=None):
        if not paths:
            print("Expected at least 1 path to an evaluation script")
            return 1

        test_path, *extra_paths = paths

        passthrough_args = extra_paths + (extra_args or [])
        if passthrough_args:
            # Strip leading dashes so mochitest args match perftest expectations.
            passthrough_args = [arg.lstrip("-") for arg in passthrough_args]

        perftest_args = [test_path]
        if passthrough_args:
            perftest_args.extend(["--mochitest-extra-args", *passthrough_args])

        # Forward directly to perftest with translated mochitest arguments.
        return self._mach_context.commands.dispatch(
            "perftest",
            self._mach_context,
            perftest_args,
        )
