# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


def snapshot(command_context, headless=False):
    from pathlib import Path
    import os
    import sys

    command_context.activate_virtualenv()

    topsrcdir = Path(command_context.topsrcdir)
    marionette = None

    try:
        if os.environ.get("MOZ_AUTOMATION"):
            # When on the try server, we need to manually install marionette.
            requirements = topsrcdir / "config" / "marionette_requirements.txt"
            if requirements.exists():
                command_context.virtualenv_manager.install_pip_requirements(
                    str(requirements)
                )

        try:
            from marionette_driver.marionette import Marionette
        except ModuleNotFoundError:
            sys.path.append(str(topsrcdir / "testing" / "marionette" / "client"))
            from marionette_driver.marionette import Marionette

        binary_path = command_context.get_binary_path()
        marionette = Marionette(
            bin=binary_path,
            # When set to "-" Firefox's log goes out to stdout.
            gecko_log="-",
            headless=headless,
        )
        marionette.start_session()
        marionette.navigate("about:blank")
        with marionette.using_context("chrome"):
            marionette.execute_script("dump('Chrome context');")
    except Exception as exc:
        print(f"Snapshot prototype failed: {exc}")
        return 1
    finally:
        if marionette:
            try:
                marionette.cleanup()
            except Exception:
                pass

    print("Snapshot prototype completed.")
    return 0
