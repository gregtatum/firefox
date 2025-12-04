# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from pathlib import Path
import os
import sys
import tempfile
import time
from typing import Any, Optional
import zipfile
from urllib.request import urlretrieve


def run_snapshot(command_context, headless: bool):
    """Entry point for the mach subcommand."""
    snapshot = Snapshot(command_context, headless)
    return snapshot.run()


class Snapshot:
    """Run SingleFile snapshots via Marionette.

    Attributes:
        command_context: Mach command context for env and paths.
        headless: Whether to run Firefox in headless mode.
        marionette: Active Marionette session.
        download_dir: Directory where snapshots are saved.
        addon_id: Installed SingleFile extension id.
        extension_base_url: moz-extension base URL for the installed SingleFile.
        singlefile_lib: Inline JavaScript source for SingleFile.
    """

    def __init__(self, command_context, headless: bool):
        """Initialize with the command context and headless flag."""
        self.command_context = command_context
        self.headless = headless
        self.marionette = None
        self.download_dir = None
        self.addon_id = None
        self.extension_base_url = None
        self.singlefile_lib = None

    def run(self):
        """Navigate sample URLs, capture snapshots, and report saved files."""
        try:
            (
                self.marionette,
                self.download_dir,
                self.addon_id,
                self.extension_base_url,
                self.singlefile_lib,
            ) = Snapshot.setup_marionette(self.command_context, self.headless)
            urls = [
                "https://gregtatum.com/writing/2024/translations/",
                "https://gregtatum.com/writing/2021/diacritical-marks/",
                "https://gregtatum.com/writing/2021/encoding-text-utf-8-unicode/",
            ]
            saved_files = []
            for index, url in enumerate(urls):
                self.marionette.navigate(url)
                self._wait_for_ready_state()
                new_file = self._trigger_singlefile_save(url, index)
                if new_file:
                    saved_files.append(new_file)
            if saved_files:
                print("Saved files:")
                for path in saved_files:
                    print(f"- {path}")
        except Exception as exc:
            print(f"Snapshotting failed: {exc}")
            return 1
        finally:
            if self.marionette:
                try:
                    self.marionette.cleanup()
                except Exception:
                    pass

        print("Snapshot prototype completed.")
        return 0

    @staticmethod
    def setup_marionette(
        command_context, headless: bool
    ) -> tuple[Any, Path, str, str, str]:
        """Create a Marionette session, install SingleFile, and return session data."""
        command_context.activate_virtualenv()
        eval_tools_dir = Path(command_context.topobjdir) / "eval-tools"
        eval_tools_dir.mkdir(parents=True, exist_ok=True)

        download_dir = eval_tools_dir / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        addon_path, singlefile_lib = Snapshot.setup_singlefile_addon(eval_tools_dir)
        Marionette, Addons = Snapshot.import_marionette(command_context)
        binary_path = command_context.get_binary_path()
        marionette = Marionette(
            bin=binary_path,
            # When set to "-" Firefox's log goes out to stdout.
            gecko_log="-",
            headless=headless,
            prefs={
                "browser.download.dir": str(download_dir),
                "browser.download.folderList": 2,
                "browser.download.useDownloadDir": True,
                "browser.download.alwaysOpenPanel": False,
                "browser.download.manager.showWhenStarting": False,
            },
        )
        marionette.start_session()
        addons = Addons(marionette)
        try:
            addon_id = addons.install(str(addon_path), temp=True)
            print(f"Installed SingleFile addon id: {addon_id}")
            with marionette.using_context("chrome"):
                base_url_script = (Path(__file__).parent / "get_base_url.js").read_text()
                base_url = marionette.execute_script(
                    base_url_script, script_args=(addon_id,)
                )
        except Exception as exc:
            print(f"Failed to install addon: {exc}")
            marionette.cleanup()
            raise
        if base_url:
            print(f"SingleFile base URL: {base_url}")

        return marionette, download_dir, addon_id, base_url, singlefile_lib

    @staticmethod
    def import_marionette(command_context):
        """Import Marionette modules, installing deps on automation if needed."""
        topsrcdir = Path(command_context.topsrcdir)
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

        return Marionette, Addons

    @staticmethod
    def setup_singlefile_addon(eval_tools_dir: Path) -> tuple[Path, str]:
        """Download and setup the XPI if it doesn't exist."""
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
                            target_name = str(
                                Path(name).relative_to(Path(name).parts[0])
                            )
                            out.writestr(target_name, zf.read(name))

        with zipfile.ZipFile(addon_path) as zf:
            singlefile_lib = zf.read("lib/single-file.js").decode("utf-8")

        return addon_path, singlefile_lib

    def _trigger_singlefile_save(self, url: str, index: int) -> Optional[Path]:
        """Inject SingleFile into the page and write the saved HTML to disk."""
        assert self.download_dir
        assert self.singlefile_lib
        with self.marionette.using_context("content"):
            save_script = (Path(__file__).parent / "singlefile_save.js").read_text()
            result = self.marionette.execute_async_script(
                save_script,
                script_args=(self.singlefile_lib,),
                new_sandbox=False,
            )

        if not result.get("ok"):
            print(f"SingleFile execution failed: {result}")
            raise RuntimeError(result.get("error", "Unknown SingleFile error"))

        data = result.get("data", {})
        content = data.get("content") or data.get("html") or data.get("pageData")
        filename = data.get("filename") or f"singlefile-{index+1}.html"
        if not filename.endswith(".html"):
            filename = f"{filename}.html"
        safe_base = "".join(
            ch if ch.isalnum() or ch in "._-" else "_" for ch in filename
        )
        safe_name = f"{index+1:02d}-{safe_base}"
        if not safe_name:
            safe_name = f"page-{index+1}.html"
        if not isinstance(content, str):
            raise RuntimeError(
                f"SingleFile returned unexpected payload keys: {result.get('keys')}"
            )

        target = self.download_dir / safe_name
        target.write_text(content)
        return target

    def _wait_for_ready_state(self):
        """Wait until the current page finishes loading."""
        with self.marionette.using_context("content"):
            self.marionette.execute_script(
                """
                return new Promise(resolve => {
                  if (document.readyState === "complete") {
                    resolve();
                    return;
                  }
                  window.addEventListener("load", () => resolve(), { once: true });
                });
                """,
                script_timeout=60000,
            )
