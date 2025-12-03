# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import getpass
import os

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
    def eval_tools_login(self):
        if os.environ.get("MOZ_FXA_BEARER_TOKEN"):
            print("MOZ_FXA_BEARER_TOKEN already set; skipping login.")
            print("Unset with: unset MOZ_FXA_BEARER_TOKEN")
            return 0

        print("Login to your Firefox Account (accounts.firefox.com)")
        email = input("Email: ").strip()
        password = getpass.getpass("Password: ").strip()
        if not email or not password:
            print("Email and password are required.")
            return 1

        self.activate_virtualenv()
        try:
            from fxa.tools.bearer import get_bearer_token
        except ModuleNotFoundError:
            try:
                self.virtualenv_manager.install_pip_package("PyFxA==0.8.1")
                from fxa.tools.bearer import get_bearer_token
            except Exception as exc:
                print(
                    f"Failed to install 'fxa' package automatically: {exc}\n"
                    "You can install it manually with: ./mach python -m pip install fxa"
                )
                return 1

        try:
            token = get_bearer_token(
                email,
                password,
                scopes=["profile"],
                client_id="5882386c6d801776",
                account_server_url="https://api.accounts.firefox.com",
                oauth_server_url="https://oauth.accounts.firefox.com",
            )
        except Exception as exc:
            print(f"Login failed: {exc}")
            return 1

        print("Copy and paste the following in your terminal to persist your login:\n")
        print(f"export MOZ_FXA_BEARER_TOKEN='{token}'")
        return 0

    @SubCommand(
        "eval-tools",
        "snapshot",
        description="Generate SingleFile snapshots of some web history",
    )
    def eval_tools_snapshot(self):
        print("Not implemented yet.")
        return 0
