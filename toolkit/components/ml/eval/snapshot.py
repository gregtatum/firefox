# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from pathlib import Path
import os
import sys
import tempfile
import time
import json
from typing import Any, Optional
import zipfile
from urllib.request import urlretrieve, urlopen
from urllib.parse import urlparse


def run_snapshot(
    command_context,
    headless: bool,
    snapshot_name: str = "snapshot",
    persona_url: Optional[str] = None,
    page_timeout_ms: int = 5000,
):
    """Entry point for the mach subcommand."""
    snapshot = Snapshot(
        command_context, headless, snapshot_name, persona_url, page_timeout_ms
    )
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
        snapshot_name: Label used to organize saved snapshots.
    """

    def __init__(
        self,
        command_context,
        headless: bool,
        snapshot_name: str,
        persona_url: Optional[str],
        page_timeout_ms: int,
    ):
        """Initialize with the command context and headless flag."""
        self.command_context = command_context
        self.headless = headless
        self.snapshot_name = snapshot_name
        self.persona_url = persona_url
        self.page_timeout_ms = page_timeout_ms
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
            ) = self.setup_marionette(self.command_context, self.headless)
            urls = self._load_urls()
            print(f"[snapshot] Loaded {len(urls)} URLs to snapshot.")
            saved_files = []
            for index, url in enumerate(urls):
                print(f"[snapshot] [{index+1}/{len(urls)}] Navigating to {url}")
                try:
                    self.marionette.navigate(url)
                    self._wait_for_ready_state()
                    new_file = self._trigger_singlefile_save(url, index)
                    if new_file:
                        print(f"[snapshot] Saved snapshot: {new_file}")
                        saved_files.append(new_file)
                except Exception as exc:
                    print(f"[snapshot] Skipping {url}: {exc}")
            if saved_files:
                print("[snapshot] Saved files:")
                for path in saved_files:
                    print(f"[snapshot] - {path}")
        except Exception as exc:
            print(f"[snapshot] Snapshotting failed: {exc}")
            return 1
        finally:
            if self.marionette:
                try:
                    self.marionette.cleanup()
                except Exception:
                    pass

        print("[snapshot] Snapshot prototype completed.")
        return 0

    def setup_marionette(
        self, command_context, headless: bool
    ) -> tuple[Any, Path, str, str, str]:
        """Create a Marionette session, install SingleFile, and return session data."""
        command_context.activate_virtualenv()
        eval_tools_dir = Path(command_context.topobjdir) / "eval-tools"
        snapshots_dir = eval_tools_dir / "snapshots" / self.snapshot_name
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        download_dir = eval_tools_dir / "download"
        download_dir.mkdir(parents=True, exist_ok=True)
        addon_path, singlefile_lib = Snapshot.setup_singlefile_addon(eval_tools_dir)
        Marionette, Addons = Snapshot.import_marionette(command_context)
        binary_path = command_context.get_binary_path()
        print(f"[snapshot] Using binary at {binary_path}")
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
            print(f"[snapshot] Installed SingleFile addon id: {addon_id}")
            with marionette.using_context("chrome"):
                base_url_script = (
                    Path(__file__).parent / "get_base_url.js"
                ).read_text()
                base_url = marionette.execute_script(
                    base_url_script, script_args=(addon_id,)
                )
                print(f"[snapshot] SingleFile base URL: {base_url}")
        except Exception as exc:
            print(f"[snapshot] Failed to install addon: {exc}")
            marionette.cleanup()
            raise

        return marionette, download_dir, addon_id, base_url, singlefile_lib

    def _load_urls(self) -> list[str]:
        """Return URLs to snapshot, optionally from a persona JSON."""
        if not self.persona_url:
            raise RuntimeError("Persona URL is required for snapshotting.")

        eval_tools_dir = Path(self.command_context.topobjdir) / "eval-tools"
        persona_dir = eval_tools_dir / "personas"
        persona_dir.mkdir(parents=True, exist_ok=True)
        parsed = urlparse(self.persona_url)
        persona_path = persona_dir / "persona.json"
        try:
            if parsed.scheme in ("http", "https"):
                urlretrieve(self.persona_url, persona_path)
            else:
                persona_path = Path(self.persona_url)
            data = json.loads(persona_path.read_text())
        except Exception as exc:
            raise RuntimeError(f"Failed to load persona URLs: {exc}")

        if isinstance(data, list):
            urls = []
            for entry in data:
                if isinstance(entry, str):
                    urls.append(entry)
                elif isinstance(entry, dict) and entry.get("url"):
                    urls.append(entry["url"])
        elif isinstance(data, dict):
            urls = data.get("urls") or data.get("pages") or []
        else:
            urls = []

        urls = [u for u in urls if isinstance(u, str) and u.strip()]
        if not urls:
            raise RuntimeError("Persona file did not contain any URLs")
        return urls

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
            print("[snapshot] Injecting SingleFile library and capturing page...")
            try:
                result = self.marionette.execute_async_script(
                    save_script,
                    script_args=(self.singlefile_lib,),
                    new_sandbox=False,
                )
            except Exception as exc:
                msg = str(exc)
                if "Document was unloaded" in msg:
                    print(f"[snapshot] Page unloaded while capturing {url}, skipping.")
                    return None
                raise

        if not result.get("ok"):
            print(f"[snapshot] SingleFile execution failed: {result}")
            raise RuntimeError(result.get("error", "Unknown SingleFile error"))

        data = result.get("data", {})
        content = data.get("content") or data.get("html") or data.get("pageData")
        parsed = urlparse(url)
        host = parsed.hostname or "unknown_host"
        path = parsed.path or ""
        path = path.lstrip("/")
        if not path or path.endswith("/"):
            path = path.rstrip("/") + "/index.html"
        if parsed.query:
            safe_query = "".join(
                ch if ch.isalnum() or ch in "._-" else "_" for ch in parsed.query
            )
            path = f"{path}__{safe_query}"
        safe_path = "".join(ch if ch.isalnum() or ch in "._-/" else "_" for ch in path)
        safe_file = safe_path.replace("/", "_")
        if not safe_file.endswith(".html"):
            safe_file = f"{safe_file}.html"
        target_dir = (
            Path(self.command_context.topobjdir)
            / "eval-tools"
            / "snapshots"
            / self.snapshot_name
            / host
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_file
        if not isinstance(content, str):
            raise RuntimeError(
                f"SingleFile returned unexpected payload keys: {result.get('keys')}"
            )

        target.write_text(content)
        return target

    def _wait_for_ready_state(self):
        """Wait until the current page finishes loading."""
        # Wait a little bit after navigating to let things settle.
        time.sleep(5)

        # with self.marionette.using_context("content"):
        #     self.marionette.execute_script(
        #         """
        #         return new Promise(resolve => {
        #           const timer = setTimeout(() => resolve(), arguments[0]);
        #           if (document.readyState === "complete") {
        #             clearTimeout(timer);
        #             resolve();
        #             return;
        #           }
        #           window.addEventListener(
        #             "load",
        #             () => {
        #               clearTimeout(timer);
        #               resolve();
        #             },
        #             { once: true }
        #           );
        #         });
        #         """,
        #         script_args=(self.page_timeout_ms,),
        #         script_timeout=script_timeout,
        #     )
