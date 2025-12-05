# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
from pathlib import Path
import sys

from mach.decorators import Command, CommandArgument, SubCommand
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
        nargs="*",
        help="Eval test paths (files or directories).",
    )
    @CommandArgument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional mochitest arguments passed through to perftest.",
    )
    def run_eval(self, paths, extra_args=None):
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


class EvalToolsCommand(MachCommandBase):
    """Helper utilities for eval workflows."""

    @Command(
        "eval-tools",
        category="testing",
        description="Helper utilities for ML evals.",
    )
    def eval_tools(self):
        print("Eval helper utilities.\n\nRun `./mach eval-tools --help` for details.")
        return 0

    @SubCommand(
        "eval-tools",
        "login",
        description="Login helper to fetch a bearer token (interactive).",
    )
    def eval_tools_login(command_context):
        sys.path.append(str(Path(command_context.topsrcdir) / "toolkit/components/ml"))
        from eval.login import login

        return login(command_context)

    @SubCommand(
        "eval-tools",
        "snapshot",
        description="Generate SingleFile snapshots of some web history",
    )
    @CommandArgument(
        "--headless",
        action="store_true",
        help="Run the browser in headless mode.",
    )
    @CommandArgument(
        "--name",
        dest="snapshot_name",
        default="snapshot",
        help="Name used to organize saved snapshots.",
    )
    @CommandArgument(
        "--persona",
        dest="persona_url",
        default=None,
        help="URL or local path to a JSON file containing URLs to snapshot.",
    )
    @CommandArgument(
        "--timeout",
        dest="page_timeout_ms",
        type=int,
        default=5000,
        help="Max time in ms to wait for each page to reach readyState complete.",
    )
    @CommandArgument(
        "--clobber",
        action="store_true",
        default=False,
        help="Re-run snapshots even if the output file already exists.",
    )
    def eval_tools_snapshot(
        command_context,
        headless=False,
        snapshot_name="snapshot",
        persona_url=None,
        page_timeout_ms=5000,
        clobber=False,
    ):
        sys.path.append(str(Path(command_context.topsrcdir) / "toolkit/components/ml"))
        from eval.snapshot import run_snapshot

        run_snapshot(
            command_context,
            headless=headless,
            snapshot_name=snapshot_name,
            persona_url=persona_url,
            page_timeout_ms=page_timeout_ms,
            clobber=clobber,
        )
