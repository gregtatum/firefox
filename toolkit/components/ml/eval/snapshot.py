# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


def snapshot(command_context):
    from pathlib import Path
    import sys

    command_context.activate_virtualenv()

    topsrcdir = Path(command_context.topsrcdir)
    marionette = None

    try:
        try:
            from marionette_driver.marionette import Marionette
        except ModuleNotFoundError:
            sys.path.append(str(topsrcdir / "testing" / "marionette" / "client"))
            from marionette_driver.marionette import Marionette

        binary_path = command_context.get_binary_path()
        marionette = Marionette(bin=binary_path)
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
