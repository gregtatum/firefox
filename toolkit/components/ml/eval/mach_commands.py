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

        self.activate_virtualenv()
        try:
            import fxa
        except ModuleNotFoundError:
            try:
                self.virtualenv_manager.install_pip_package("PyFxA==0.8.1")
            except Exception as exception:
                print(f"Failed to install 'fxa' package: {exception}")
                return 1

        from fxa import core, oauth
        from fxa.errors import ClientError
        from fxa.tools.bearer import get_bearer_token
        from fxa.tools.unblock import send_unblock_code

        scopes = ["profile"]
        client_id = "5882386c6d801776"
        account_server_url = "https://api.accounts.firefox.com"
        oauth_server_url = "https://oauth.accounts.firefox.com"

        print("Login to your Firefox Account (accounts.firefox.com)")
        email = input("Email: ").strip()
        password = getpass.getpass("Password: ").strip()

        try:
            token = get_bearer_token(
                email,
                password,
                scopes=scopes,
                client_id=client_id,
                account_server_url=account_server_url,
                oauth_server_url=oauth_server_url,
            )
        except ClientError as exception:
            try:
                if "Unconfirmed session" not in str(exception):
                    raise

                try:
                    send_unblock_code(email, account_server_url)
                except ClientError:
                    print("Login failed: unable to send unblock code.")
                    return 1

                print("\nAn authorization code was sent to your email, enter it here.")
                unblock_code = input("Code: ").strip()

                try:
                    # Attempt to login without 2 factor authentication.
                    session = core.Client(server_url=account_server_url).login(
                        email,
                        password,
                        unblock_code=unblock_code,
                    )
                    token = oauth.Client(
                        client_id=client_id,
                        server_url=oauth_server_url,
                    ).authorize_token(session, " ".join(scopes))
                except ClientError:
                    # Two factor is required, try again.
                    session = core.Client(server_url=account_server_url).login(
                        email,
                        password,
                        unblock_code=unblock_code,
                        verification_method="totp-2fa",
                    )

                    print(
                        "\nTwo-factor authorization is enabled, open your app and enter the code:"
                    )
                    totp_code = input("Code: ").strip()
                    if not session.totp_verify(totp_code):
                        print("Login failed: invalid two-factor code.")
                        return 1

                    token = oauth.Client(
                        client_id=client_id,
                        server_url=oauth_server_url,
                    ).authorize_token(session, " ".join(scopes))

            except Exception as retry_exception:
                print(retry_exception)
                print(f"Login failed: {retry_exception}")
                return 1
        except Exception as exception:
            print(exception)
            print(f"Login failed: {exception}")
            return 1

        print(
            "\nCopy and paste the following in your terminal to persist your login:\n"
        )
        print(f" export MOZ_FXA_BEARER_TOKEN='{token}'")
        return 0
