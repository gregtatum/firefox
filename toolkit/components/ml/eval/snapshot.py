# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from pathlib import Path
from typing import Any
import os
import shutil
import sys
import tempfile
import zipfile
from urllib.request import urlretrieve


def setup_singlefile_addon(eval_tools_dir: Path) -> Path:
    """
    Download and setup the XPI if it doesn't exist.
    """
    addon_path = eval_tools_dir / "SingleFile.xpi"
    if not addon_path.exists():
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / "SingleFile-1.2.0.zip"
            urlretrieve(
                "https://github.com/gildas-lormeau/SingleFile/archive/refs/tags/v1.2.0.zip",
                archive_path,
            )

            # The zip file needs repacking into an XPI.
            with zipfile.ZipFile(archive_path) as zf:
                names = zf.namelist()
                top_dirs = {
                    Path(n).parts[0] for n in names if n and not n.endswith("/")
                }
                assert (
                    len(top_dirs) == 1
                ), "The file had multiple root directories, it does not need repacking."
                assert [
                    n for n in names if n.endswith("manifest.json")
                ], "A manifest file was not found"

                # Perform the repacking.
                with zipfile.ZipFile(addon_path, "w") as out:
                    for name in names:
                        if name.endswith("/"):
                            # This is a directory
                            continue
                        target_name = str(Path(name).relative_to(Path(name).parts[0]))
                        out.writestr(target_name, zf.read(name))

    return addon_path


def snapshot(command_context, headless=False):
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
            from marionette_driver.addons import Addons
        except ModuleNotFoundError:
            sys.path.append(str(topsrcdir / "testing" / "marionette" / "client"))
            from marionette_driver.marionette import Marionette
            from marionette_driver.addons import Addons

        eval_tools_dir = Path(command_context.topobjdir) / "eval-tools"
        eval_tools_dir.mkdir(parents=True, exist_ok=True)

        addon_path = setup_singlefile_addon(eval_tools_dir)

        binary_path = command_context.get_binary_path()
        marionette = Marionette(
            bin=binary_path,
            # When set to "-" Firefox's log goes out to stdout.
            gecko_log="-",
            headless=headless,
        )
        marionette.start_session()
        addons = Addons(marionette)
        try:
            addons.install(str(addon_path), temp=True)
        except Exception as exc:
            print(f"Failed to install addon: {exc}")
            return 1
        marionette.navigate("about:blank")
        with marionette.using_context("chrome"):
            marionette.execute_script("dump('Chrome context');")

        from time import sleep

        sleep(100)
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
