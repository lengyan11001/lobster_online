#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Serve the online client as static files and proxy API calls to lobster_server.

Usage:
    python scripts/serve_online_client.py [port] [api_base]

The static page is served from this checkout. Requests for remote API routes are
proxied to api_base so modules that use relative URLs still work without a local
backend process.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DEFAULT_API = "https://bhzn.top"
DEFAULT_PORT = 8000
PROXY_PREFIXES = (
    "/api/",
    "/auth/",
    "/skills/",
    "/capabilities/",
    "/chat/",
)
LOCAL_ONLY_OPTIONAL_PATHS = {
    "/auth/persist-openclaw-channel-fallback",
    "/api/settings/sync-tos-from-server",
    "/api/openclaw/memory/sync-cloud",
}


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    api_base = (sys.argv[2] if len(sys.argv) > 2 else DEFAULT_API).rstrip("/")
    if not (STATIC / "index.html").exists():
        print(f"[ERR] missing {STATIC / 'index.html'}", file=sys.stderr)
        sys.exit(1)

    from http.server import HTTPServer, SimpleHTTPRequestHandler

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(ROOT), **k)

        def _path_part(self):
            return (self.path or "/").split("?", 1)[0].rstrip("/") or "/"

        def _send_json(self, status, payload):
            data = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _is_remote_proxy_path(self, path_part):
            return any(path_part.startswith(prefix) for prefix in PROXY_PREFIXES)

        def _proxy_to_api(self):
            path = self.path or "/"
            path_part = self._path_part()
            if path_part in LOCAL_ONLY_OPTIONAL_PATHS:
                self._send_json(204, "")
                return

            target = api_base + path
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {}
            for key in ("Authorization", "Content-Type", "X-Installation-Id", "Accept"):
                value = self.headers.get(key)
                if value:
                    headers[key] = value
            try:
                req = Request(target, data=body, headers=headers, method=self.command)
                with urlopen(req, timeout=45) as resp:
                    data = resp.read()
                    self.send_response(resp.status)
                    for key, value in resp.headers.items():
                        lk = key.lower()
                        if lk in {"connection", "transfer-encoding", "content-encoding", "content-length"}:
                            continue
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
            except HTTPError as e:
                data = e.read()
                self.send_response(e.code)
                self.send_header("Content-Type", e.headers.get("Content-Type") or "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (URLError, TimeoutError) as e:
                msg = '{"ok":false,"detail":"remote api proxy failed: %s"}' % str(e).replace('"', "'")
                self._send_json(502, msg)

        def do_GET(self):
            path_part = self._path_part()
            if self._is_remote_proxy_path(path_part):
                return self._proxy_to_api()
            if path_part in {"/", "/index.html"}:
                self.path = "/static/index.html"
            return SimpleHTTPRequestHandler.do_GET(self)

        def do_POST(self):
            if self._is_remote_proxy_path(self._path_part()):
                return self._proxy_to_api()
            self.send_error(501, "Unsupported method ('POST')")

        def do_PUT(self):
            if self._is_remote_proxy_path(self._path_part()):
                return self._proxy_to_api()
            self.send_error(501, "Unsupported method ('PUT')")

        def do_DELETE(self):
            if self._is_remote_proxy_path(self._path_part()):
                return self._proxy_to_api()
            self.send_error(501, "Unsupported method ('DELETE')")

        def list_directory(self, path):
            self.send_error(404, "Not Found")
            return None

        def log_message(self, format, *args):
            print(format % args)

    server = HTTPServer(("", port), Handler)
    print("================================================")
    print("  Lobster Online Client (port %s)" % port)
    print("  http://127.0.0.1:%s" % port)
    print("  API: %s" % api_base)
    print("================================================")
    print("  Ctrl+C to stop")
    server.serve_forever()


if __name__ == "__main__":
    main()
