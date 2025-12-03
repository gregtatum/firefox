# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import getpass
import os


def login(command_context):
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

    command_context.activate_virtualenv()
    try:
        from fxa.tools.bearer import get_bearer_token
    except ModuleNotFoundError:
        try:
            command_context.virtualenv_manager.install_pip_package("PyFxA==0.8.1")
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
