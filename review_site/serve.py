#!/usr/bin/env python3
"""Local review server: serves the review site and writes reviewers' comments to disk.

  python3 serve.py [--site DIR] [--port 8765] [--host 127.0.0.1]

Every save updates <site>/reviews/reviews.json and rewrites <site>/reviewed/<dataset>_reviewed.jsonl.
Served by any static file server instead (no review API), the page keeps reviews in the browser and
its Export button downloads the reviewed JSONL.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from reviews import Site  # noqa: E402

SKELETON_HEAD = ('<!doctype html><html><head><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                 '</head><body>')
LOCK = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, site: Site, **kw):
        self.site = site
        super().__init__(*a, directory=str(site.root), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            page = (self.site.root / "index.html").read_text()
            body = (page if page.lstrip().lower().startswith("<!doctype") else SKELETON_HEAD + page + "</body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/ping":
            return self._json(200, {"mode": "local", "store": str(self.site.store)})
        if self.path == "/api/reviews":
            with LOCK:
                return self._json(200, self.site.load_store())
        if self.path.startswith("/api/export/"):
            ds = self.path.rsplit("/", 1)[-1]
            p = self.site.reviewed_file(ds) if ds in self.site.datasets() else None
            if p is None or not p.exists():
                return self._json(404, {"error": "no reviewed file yet"})
            body = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_PUT(self):
        parts = self.path.strip("/").split("/")
        if len(parts) != 4 or parts[:2] != ["api", "reviews"] or parts[2] not in self.site.datasets():
            return self._json(404, {"error": "PUT /api/reviews/<dataset>/<record_id>"})
        ds, qid = parts[2], parts[3]
        try:
            review = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
        except json.JSONDecodeError:
            return self._json(400, {"error": "body must be JSON"})
        with LOCK:
            store = self.site.load_store()
            key = f"{ds}~{qid}"
            if review:
                review["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                store[key] = review
            else:
                store.pop(key, None)
            self.site.save_store(store)
            self.site.export(store, [ds])
        return self._json(200, {"ok": True, "saved": key,
                                "file": str(self.site.reviewed_file(ds).relative_to(self.site.root))})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--site", default=str(HERE), help="review site directory (default: this directory)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    site = Site(Path(a.site))
    if not site.datasets():
        sys.exit(f"{site.data / 'manifest.json'} not found: run build_site_data.py (or demo/make_demo.py) first")
    with LOCK:
        site.export(site.load_store())
    srv = ThreadingHTTPServer((a.host, a.port), partial(Handler, site=site))
    print(f"Review site: http://{a.host}:{a.port}/  (comments -> {site.store}, {site.reviewed}/)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
