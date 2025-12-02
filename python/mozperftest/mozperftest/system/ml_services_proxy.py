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


def _parse_routes(raw_routes):
    routes = []
    for entry in raw_routes or []:
        if "=" not in entry:
            raise ValueError(f"Invalid route {entry}, expected prefix=url")
        prefix, target = entry.split("=", 1)
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        routes.append((prefix, target))
    return routes


def _normalize_base(url):
    if not url:
        return DEFAULT_REMOTE_SETTINGS
    parsed = urlparse(url)
    if not parsed.scheme:
        raise ValueError(f"Upstream url must include scheme: {url}")
    return url.rstrip("/")


class MLServicesProxy(Layer):
    name = "ml-services-proxy"
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

    def _forward(self, handler, upstream, parsed):
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

        self.info(f"[ml-services-proxy] {handler.command} {target_url}")
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
                f"Proxy upstream error {resp.status_code} for {handler.command} {target_url}: {sample}"
            )
            self.errors.append(
                {
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

    def run(self, metadata):
        upstream = _normalize_base(DEFAULT_REMOTE_SETTINGS)
        routes = [("/v1", upstream)]
        fixtures = []
        host = "127.0.0.1"
        port = 0
        timeout = 30.0

        upstream_path = urlparse(upstream).path or "/"
        upstream_path = upstream_path.rstrip("/") or "/"

        def resolve(parsed):
            req_path = parsed.path.rstrip("/") or "/"
            if req_path == upstream_path:
                return ("root", upstream_path, upstream)
            cdn_prefix = f"{upstream_path}/cdn/"
            if parsed.path.startswith(cdn_prefix):
                return ("cdn", cdn_prefix, upstream)
            for prefix, root in fixtures:
                if parsed.path.startswith(prefix):
                    return ("fixture", prefix, root)
            for prefix, target in routes:
                if parsed.path.startswith(prefix):
                    return ("proxy", prefix, target)
            return ("proxy", None, upstream)

        proxy = self

        class Handler(BaseHTTPRequestHandler):
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
                entry = {
                    "method": self.command,
                    "path": self.path,
                    "mode": mode,
                    "target": target,
                }
                proxy.request_log.append(entry)
                try:
                    if mode == "root":
                        upstream_root = (
                            upstream if upstream.endswith("/") else upstream + "/"
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
                        try:
                            proxy.request_log.append(
                                {
                                    "path": parsed.path,
                                    "status": resp.status_code,
                                    "ct": resp.headers.get("Content-Type"),
                                    "sample": body[:200].decode("utf-8", "replace"),
                                }
                            )
                        except Exception:
                            pass
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

                        proxy.info(f"[ml-services-proxy] {self.command} {target_url}")

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
                                f"Proxy upstream error {resp.status_code} for attachments {self.command} {target_url}: {sample}"
                            )
                            proxy.errors.append(
                                {
                                    "path": parsed.path,
                                    "status": resp.status_code,
                                    "target": target_url,
                                    "sample": sample,
                                }
                            )
                        try:
                            proxy.request_log.append(
                                {
                                    "path": parsed.path,
                                    "status": resp.status_code,
                                    "ct": resp.headers.get("Content-Type"),
                                    "sample": content[:200].decode("utf-8", "replace"),
                                }
                            )
                        except Exception:
                            pass
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
                        proxy._forward(self, target, parsed)
                except Exception as exc:
                    proxy.errors.append(
                        {
                            "path": parsed.path,
                            "status": 502,
                            "target": target,
                            "sample": str(exc),
                        }
                    )
                    proxy.error(
                        f"Proxy exception for {self.command} {self.path}: {exc}"
                    )
                    self.send_error(502, explain=str(exc))

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.timeout = timeout
        self.bound_host, self.bound_port = self.server.server_address
        self.local_base = (
            f"http://{self.bound_host}:{self.bound_port}{urlparse(upstream).path}"
        )

        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

        browser_prefs = metadata.get_options("browser_prefs")
        browser_prefs["services.settings.server"] = self.local_base
        metadata.update_options(
            "extra_prefs",
            {"services.settings.server": self.local_base},
        )
        os.environ["MOZ_REMOTE_SETTINGS_DEVTOOLS"] = "1"
        os.environ["ML_SERVICES_PROXY_URL"] = self.local_base
        self.info(f"[ml-services-proxy] Running proxy at: {self.local_base}")
        return metadata

    def teardown(self):
        if self.server is not None:
            self.server.shutdown()
            self.thread.join()
            self.server.server_close()
        # Optional future: persist request logs if we want to inspect them.
        if self.errors:
            first = self.errors[0]
            raise RuntimeError(
                f"ML services proxy saw upstream errors, first: {first['status']} {first['target']} ({first['sample']})"
            )
