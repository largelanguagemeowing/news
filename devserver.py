#!/usr/bin/env python3
"""Serve dashboard under /news/ for local dev.

GitHub Pages hosts this site at https://.../news/ (a subpath). Many dashboard
pages use relative URLs that behave differently when served at /.

This server mounts:
- /news/ -> ./dashboard/
- /news/data/ -> ./data/

Run:
  python3 devserver.py --port 8008
Then open:
  http://127.0.0.1:8008/news/
"""

from __future__ import annotations

import argparse
import http.server
import os
import posixpath
import urllib.parse


class NewsSubpathHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, dashboard_root: str, data_root: str, **kwargs):
        self._dashboard_root = os.path.abspath(dashboard_root)
        self._data_root = os.path.abspath(data_root)
        super().__init__(*args, **kwargs)

    def translate_path(self, path: str) -> str:
        parsed = urllib.parse.urlparse(path)
        req_path = urllib.parse.unquote(parsed.path)

        if req_path == "/news":
            req_path = "/news/"

        if req_path.startswith("/news/data/"):
            rel = req_path[len("/news/data/") :]
            rel = posixpath.normpath(rel).lstrip("/")
            return os.path.join(self._data_root, rel)

        if req_path.startswith("/news/"):
            rel = req_path[len("/news/") :]
            rel = posixpath.normpath(rel).lstrip("/")
            return os.path.join(self._dashboard_root, rel)

        # Not found outside /news
        return os.path.join(self._dashboard_root, "__not_found__")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Keep logs readable while still surfacing errors.
        super().log_message(format, *args)

    def send_head(self):
        # Guard against occasional transient issues where SimpleHTTPRequestHandler
        # can raise during open/stat, which shows up as a 503 to the browser.
        try:
            return super().send_head()
        except Exception as e:
            self.send_error(500, f"Internal server error: {e}")
            return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8008)
    ap.add_argument("--dashboard", default="dashboard")
    ap.add_argument("--data", default="data")
    args = ap.parse_args()

    handler = lambda *h_args, **h_kwargs: NewsSubpathHandler(
        *h_args,
        dashboard_root=args.dashboard,
        data_root=args.data,
        **h_kwargs,
    )

    with http.server.ThreadingHTTPServer((args.host, args.port), handler) as httpd:
        print(f"Serving at http://{args.host}:{args.port}/news/")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
