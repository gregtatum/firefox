# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import os
import shutil
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests

from mozperftest.layers import Layer


class Persona(Layer):
    name = "persona"
    activated = True
    arguments = {
        "url": {
            "type": str,
            "default": None,
            "help": (
                "Override the persona archive URL. Defaults to the value in the test "
                "metadata if available."
            ),
        },
    }

    def __init__(self, env, mach_cmd):
        super().__init__(env, mach_cmd)
        self.server = None
        self.thread = None
        self.persona_root = None
        self.persona_url = None
        self.bound_host = None
        self.bound_port = None

    def run(self, metadata):
        self.persona_url = (
            self.get_arg("url")
            or metadata.script.get("persona")
            or metadata.script.get("options", {}).get("default", {}).get("persona")
        )
        if not self.persona_url:
            self.info("No persona archive provided; skipping persona layer.")
            return metadata

        self.persona_root = self._fetch_and_extract(self.persona_url)
        self._start_server()
        return metadata

    def _fetch_and_extract(self, source):
        target_dir = Path(self.mach_cmd.topobjdir) / "eval-tools" / "personas"
        target_dir.mkdir(parents=True, exist_ok=True)

        parsed = urlparse(source)
        if parsed.scheme in ("http", "https"):
            archive_name = Path(parsed.path).name or "persona.zip"
            archive_path = target_dir / archive_name
            if archive_path.exists():
                self.info(f"Using cached persona archive at {archive_path}")
            else:
                self.info(f"Downloading persona archive from {source}")
                resp = requests.get(source, timeout=120)
                resp.raise_for_status()
                archive_path.write_bytes(resp.content)
        else:
            archive_path = Path(source).expanduser()
            if not archive_path.exists():
                raise RuntimeError(f"Persona archive not found: {archive_path}")

        extract_dir = target_dir / archive_path.stem
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_dir)

        root = self._find_persona_root(extract_dir)
        if root is None:
            raise RuntimeError(f"persona.json not found in {extract_dir}")

        return root

    def _find_persona_root(self, extract_dir):
        persona_path = extract_dir / "persona.json"
        if persona_path.exists():
            return extract_dir

        for entry in extract_dir.iterdir():
            if entry.is_dir() and (entry / "persona.json").exists():
                return entry

        return None

    def _start_server(self):
        handler = partial(SimpleHTTPRequestHandler, directory=str(self.persona_root))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.bound_host, self.bound_port = self.server.server_address
        base = f"http://{self.bound_host}:{self.bound_port}"

        os.environ["EVAL_PERSONA_PROXY_URL"] = base
        persona_json = self.persona_root / "persona.json"
        if persona_json.exists():
            rel = persona_json.relative_to(self.persona_root).as_posix()
            os.environ["EVAL_PERSONA_URL"] = f"{base}/{rel}"

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def teardown(self):
        if self.server is not None:
            self.server.shutdown()
            self.thread.join()
            self.server.server_close()
            self.server = None
            self.thread = None
        os.environ.pop("EVAL_PERSONA_PROXY_URL", None)
        os.environ.pop("EVAL_PERSONA_URL", None)
        self.persona_root = None
        self.persona_url = None
