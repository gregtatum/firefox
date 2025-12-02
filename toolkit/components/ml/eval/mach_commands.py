# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mach.decorators import Command, CommandArgument
from mozbuild.base import MachCommandBase


class EvalCommand(MachCommandBase):
    """Shim command that forwards eval runs to mozperftest."""

    @Command(
        "eval",
        category="testing",
        description="Run ML evals (shim to ./mach perftest).",
    )
    @CommandArgument(
        "paths",
        nargs="+",
        help="Eval test paths (files or directories).",
    )
    @CommandArgument(
        "--login",
        action="store_true",
        default=False,
        help="Login helper (stubbed; currently prints TODO).",
    )
    def run_eval(self, paths, login=False):
        if login:
            print("TODO (login)")
            return 0

        # Forward directly to perftest, preserving only the provided paths.
        return self._mach_context.commands.dispatch(
            "perftest",
            self._mach_context,
            list(paths),
        )
