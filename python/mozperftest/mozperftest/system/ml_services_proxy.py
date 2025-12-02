# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlsplit

import requests

from mozperftest.layers import Layer


DEFAULT_REMOTE_SETTINGS = "https://firefox.settings.services.mozilla.com/v1"
DEFAULT_MODEL_HUB = "https://model-hub.mozilla.org"
HOP_BY_HOP_HEADERS = {
    "connection",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _normalize_base(url):
    parsed = urlparse(url)
    if not parsed.scheme:
        raise ValueError(f"Upstream url must include scheme: {url}")
    return url.rstrip("/")


class _BaseProxy(Layer):
    activated = False
    arguments = {}

    def __init__(self, env, mach_cmd):
        super().__init__(env, mach_cmd)
        self.server = None
        self.thread = None
        self.request_log = []
        self.bound_host = None
        self.bound_port = None
        self.local_base = None
        self.attachments_upstream = None
        self.errors = []

    def setup(self):
        os.environ["MOZ_REMOTE_SETTINGS_DEVTOOLS"] = "1"

    def _serve_fixture(self, handler, prefix, root, parsed):
        rel = parsed.path[len(prefix) :].lstrip("/")
        base = Path(root)
        if not base.exists():
            handler.send_error(502, "Fixture root missing")
            return
        candidates = [base / rel]
        if not rel.endswith(".json"):
            candidates.append(base / f"{rel}.json")
        target = next((c for c in candidates if c.exists() and c.is_file()), None)
        if target is None:
            handler.send_error(404, "Fixture not found")
            return
        data = target.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(data)

    def _forward(self, handler, upstream, parsed, upstream_name):
        base = upstream if upstream.endswith("/") else upstream + "/"
        target_url = urljoin(base, parsed.path)
        if parsed.query:
            target_url = f"{target_url}?{parsed.query}"

        content_length = int(handler.headers.get("Content-Length", 0))
        body = handler.rfile.read(content_length) if content_length > 0 else None
        headers = {
            k: v
            for k, v in handler.headers.items()
            if k.lower() not in HOP_BY_HOP_HEADERS
        }

        self.info(
            f"[ml-services-proxy][{upstream_name}] {handler.command} {target_url}"
        )
        resp = requests.request(
            method=handler.command,
            url=target_url,
            headers=headers,
            data=body,
            timeout=30.0,
        )
        content = resp.content if handler.command != "HEAD" else b""
        if resp.status_code >= 400:
            sample = content[:200].decode("utf-8", "replace")
            self.error(
                f"[{upstream_name}] Proxy upstream error {resp.status_code} for {handler.command} {target_url}: {sample}"
            )
            self.errors.append(
                {
                    "upstream": upstream_name,
                    "path": parsed.path,
                    "status": resp.status_code,
                    "target": target_url,
                    "sample": sample,
                }
            )

        try:
            self.request_log.append(
                {
                    "path": parsed.path,
                    "status": resp.status_code,
                    "ct": resp.headers.get("Content-Type"),
                    "sample": content[:200].decode("utf-8", "replace"),
                }
            )
        except Exception:
            pass

        handler.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() in HOP_BY_HOP_HEADERS:
                continue
            if k.lower() == "content-length":
                continue
            handler.send_header(k, v)
        handler.send_header("Content-Length", str(len(content)))
        handler.end_headers()
        if content:
            handler.wfile.write(content)

    def teardown(self):
        if self.server is not None:
            self.server.shutdown()
            self.thread.join()
            self.server.server_close()
        # reset per-run state
        self.server = None
        self.thread = None
        self.request_log = []
        self.bound_host = None
        self.bound_port = None
        self.local_base = None
        if self.errors:
            first = self.errors[0]
            raise RuntimeError(
                f"ML services proxy saw upstream errors ({first.get('upstream','unknown')}): "
                f"{first['status']} {first['target']} ({first['sample']})"
            )


class RemoteSettingsProxy(_BaseProxy):
    name = "ml-services-proxy-settings"
    activated = True

    def run(self, metadata):
        settings_upstream = _normalize_base(DEFAULT_REMOTE_SETTINGS)
        routes = [("/v1", settings_upstream)]
        fixtures = []
        host = "127.0.0.1"
        port = 0
        timeout = 30.0

        upstream_path = urlparse(settings_upstream).path or "/"
        upstream_path = upstream_path.rstrip("/") or "/"

        def resolve(parsed):
            req_path = parsed.path.rstrip("/") or "/"
            if req_path == upstream_path:
                return ("root", upstream_path, settings_upstream)
            cdn_prefix = f"{upstream_path}/cdn/"
            if parsed.path.startswith(cdn_prefix):
                return ("cdn", cdn_prefix, settings_upstream)
            for prefix, root in fixtures:
                if parsed.path.startswith(prefix):
                    return ("fixture", prefix, root)
            for prefix, target in routes:
                if parsed.path.startswith(prefix):
                    return ("proxy", prefix, target)
            return ("proxy", None, settings_upstream)

        proxy = self

        class SettingsHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                entry = {
                    "client": self.client_address[0],
                    "method": self.command,
                    "path": self.path,
                    "message": fmt % args,
                }
                proxy.request_log.append(entry)

            def do_HEAD(self):
                self._handle()

            def do_GET(self):
                self._handle()

            def do_POST(self):
                self._handle()

            def do_PUT(self):
                self._handle()

            def do_DELETE(self):
                self._handle()

            def _handle(self):
                parsed = urlsplit(self.path)
                mode, prefix, target = resolve(parsed)
                proxy.request_log.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "mode": mode,
                        "target": target,
                    }
                )
                try:
                    if mode == "root":
                        upstream_root = (
                            settings_upstream
                            if settings_upstream.endswith("/")
                            else settings_upstream + "/"
                        )
                        resp = requests.get(upstream_root, timeout=30.0)
                        data = resp.json()
                        attachments = data.get("capabilities", {}).get(
                            "attachments", {}
                        )
                        proxy.attachments_upstream = attachments.get("base_url")
                        local_cdn = (
                            f"http://{proxy.bound_host}:{proxy.bound_port}"
                            f"{upstream_path}/cdn/"
                        )
                        if (
                            "capabilities" in data
                            and "attachments" in data["capabilities"]
                        ):
                            data["capabilities"]["attachments"]["base_url"] = local_cdn
                        body = json.dumps(data).encode("utf-8")
                        self.send_response(resp.status_code)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        if self.command != "HEAD":
                            self.wfile.write(body)
                    elif mode == "cdn":
                        if not proxy.attachments_upstream:
                            self.send_error(
                                502, explain="Missing upstream attachments URL"
                            )
                            return
                        base = proxy.attachments_upstream
                        if not base.endswith("/"):
                            base += "/"
                        rest = parsed.path[len(prefix) :]
                        target_url = urljoin(base, rest)
                        if parsed.query:
                            target_url = f"{target_url}?{parsed.query}"

                        proxy.info(
                            f"[ml-services-proxy][settings] {self.command} {target_url}"
                        )

                        content_length = int(self.headers.get("Content-Length", 0))
                        body = (
                            self.rfile.read(content_length)
                            if content_length > 0
                            else None
                        )
                        headers = {
                            k: v
                            for k, v in self.headers.items()
                            if k.lower() not in HOP_BY_HOP_HEADERS
                        }
                        resp = requests.request(
                            method=self.command,
                            url=target_url,
                            headers=headers,
                            data=body,
                            timeout=30.0,
                        )
                        content = resp.content if self.command != "HEAD" else b""
                        if resp.status_code >= 400:
                            sample = content[:200].decode("utf-8", "replace")
                            proxy.error(
                                f"[settings] Proxy upstream error {resp.status_code} for attachments {self.command} {target_url}: {sample}"
                            )
                            proxy.errors.append(
                                {
                                    "upstream": "settings",
                                    "path": parsed.path,
                                    "status": resp.status_code,
                                    "target": target_url,
                                    "sample": sample,
                                }
                            )
                        self.send_response(resp.status_code)
                        for k, v in resp.headers.items():
                            if k.lower() in HOP_BY_HOP_HEADERS:
                                continue
                            if k.lower() == "content-length":
                                continue
                            self.send_header(k, v)
                        self.send_header("Content-Length", str(len(content)))
                        self.end_headers()
                        if content:
                            self.wfile.write(content)
                    elif mode == "fixture":
                        proxy._serve_fixture(self, prefix, target, parsed)
                    else:
                        proxy._forward(self, target, parsed, upstream_name="settings")
                except Exception as exc:
                    proxy.errors.append(
                        {
                            "upstream": "settings",
                            "path": parsed.path,
                            "status": 502,
                            "target": target,
                            "sample": str(exc),
                        }
                    )
                    proxy.error(
                        f"[settings] Proxy exception for {self.command} {self.path}: {exc}"
                    )
                    self.send_error(502, explain=str(exc))

        self.server = ThreadingHTTPServer((host, port), SettingsHandler)
        self.server.timeout = timeout
        self.bound_host, self.bound_port = self.server.server_address
        self.local_base = f"http://{self.bound_host}:{self.bound_port}{urlparse(settings_upstream).path}"

        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

        browser_prefs = metadata.get_options("browser_prefs")
        browser_prefs["services.settings.server"] = self.local_base
        metadata.update_options(
            "extra_prefs",
            {"services.settings.server": self.local_base},
        )
        os.environ["ML_SERVICES_PROXY_REMOTE_SETTINGS"] = self.local_base
        os.environ["ML_SERVICES_PROXY_URL"] = self.local_base
        self.info(f"[ml-services-proxy] Settings proxy at: {self.local_base}")
        return metadata


class ModelHubProxy(_BaseProxy):
    name = "ml-services-proxy-model-hub"
    activated = True

    def run(self, metadata):
        model_hub_upstream = _normalize_base(DEFAULT_MODEL_HUB)
        host = "127.0.0.1"
        port = 0
        timeout = 30.0

        proxy = self

        class ModelHubHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                entry = {
                    "client": self.client_address[0],
                    "method": self.command,
                    "path": self.path,
                    "message": fmt % args,
                }
                proxy.request_log.append(entry)

            def do_HEAD(self):
                self._handle()

            def do_GET(self):
                self._handle()

            def do_POST(self):
                self._handle()

            def do_PUT(self):
                self._handle()

            def do_DELETE(self):
                self._handle()

            def _handle(self):
                parsed = urlsplit(self.path)
                proxy.request_log.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "mode": "model-hub",
                        "target": model_hub_upstream,
                    }
                )
                try:
                    proxy._forward(
                        self, model_hub_upstream, parsed, upstream_name="model-hub"
                    )
                except Exception as exc:
                    proxy.errors.append(
                        {
                            "upstream": "model-hub",
                            "path": parsed.path,
                            "status": 502,
                            "target": model_hub_upstream,
                            "sample": str(exc),
                        }
                    )
                    proxy.error(
                        f"[model-hub] Proxy exception for {self.command} {self.path}: {exc}"
                    )
                    self.send_error(502, explain=str(exc))

        self.server = ThreadingHTTPServer((host, port), ModelHubHandler)
        self.server.timeout = timeout
        self.bound_host, self.bound_port = self.server.server_address
        self.local_base = f"http://{self.bound_host}:{self.bound_port}/"

        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

        browser_prefs = metadata.get_options("browser_prefs")
        browser_prefs["browser.ml.modelHubRootUrl"] = self.local_base
        metadata.update_options(
            "extra_prefs",
            {"browser.ml.modelHubRootUrl": self.local_base},
        )
        os.environ["ML_SERVICES_PROXY_MODEL_HUB"] = self.local_base
        self.info(f"[ml-services-proxy] Model hub proxy at: {self.local_base}")
        return metadata
