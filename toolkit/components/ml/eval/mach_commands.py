# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import getpass

from mach.decorators import Command, CommandArgument
from mozbuild.base import MachCommandBase


def get_bearer_token(
    email,
    password,
    scopes=None,
    account_server_url="https://api.accounts.firefox.com",
    oauth_server_url="https://oauth.accounts.firefox.com",
    client_id="5882386c6d801776",
    client_secret=None,
    use_pkce=False,
    unblock_code=None,
):
    from fxa import core, oauth

    message = None

    if not account_server_url:
        message = "Please define an account_server_url."

    elif not oauth_server_url:
        message = "Please define an oauth_server_url."

    elif not client_id:
        message = "Please define a client_id."

    if message:
        raise ValueError(message)

    if scopes is None:
        scopes = ["profile"]

    client = core.Client(server_url=account_server_url)
    session = client.login(email, password, unblock_code=unblock_code)

    oauth_client = oauth.Client(client_id, client_secret, server_url=oauth_server_url)

    # XXX TODO: we should be able to automaticaly choose the most
    # direct route to getting a token, based on registered client
    # metadata.  Unfortunately the oauth-server doesn't (yet) expose
    # client properties like `canGrant` and `isPublic`.
    # print metadata
    # metadata = oauth_client.get_client_metadata()

    scope = " ".join(scopes)
    if client_secret is None and not use_pkce:
        token = oauth_client.authorize_token(session, scope)
    else:
        challenge = verifier = {}
        if use_pkce:
            (challenge, verifier) = oauth_client.generate_pkce_challenge()
        code = oauth_client.authorize_code(session, scope, **challenge)
        token = oauth_client.trade_code(code, **verifier)

    return token


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
        "--login",
        action="store_true",
        default=False,
        help="Login helper to fetch a bearer token (interactive).",
    )
    def run_eval(self, paths, login=False):
        if login:
            email = input("FxA email: ").strip()
            password = getpass.getpass("FxA password: ").strip()
            if not email or not password:
                print("Email and password are required.")
                return 1

            # Ensure the fxa dependency is available in the mach virtualenv.
            self.activate_virtualenv()
            try:
                import fxa  # noqa: F401
            except ModuleNotFoundError:
                try:
                    self.virtualenv_manager.install_pip_package("PyFxA==0.8.1")
                except Exception as exc:
                    print(
                        f"Failed to install 'fxa' package automatically: {exc}\n"
                        "You can install it manually with: ./mach python -m pip install fxa"
                    )
                    return 1

            try:
                token = get_bearer_token(email=email, password=password)
            except Exception as exc:
                print(f"Login failed: {exc}")
                return 1
            print(token)
            return 0

        if not paths:
            print("Expected at least 1 path to an evaluation script")
            return 1

        # Forward directly to perftest, preserving only the provided paths.
        return self._mach_context.commands.dispatch(
            "perftest",
            self._mach_context,
            list(paths),
        )
