"""Vercel entry point.

Vercel's Python runtime looks for a module-level `handler` subclassing
BaseHTTPRequestHandler, one file per route. This is a thin adapter: it adds the
repo root to sys.path and delegates to the same build_payload() the CLI
scorecard and the local server use. It computes nothing of its own, and there is
no second source of truth here either.

Differences from server.py, both forced by the platform:
  * no in-process cache -- each invocation may be a fresh container, so every
    request rebuilds. That costs ~2s and is the honest behaviour anyway.
  * no threading lock -- concurrent requests land in separate containers.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("", "harness"):
    p = str(ROOT / sub) if sub else str(ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

WEB = ROOT / "web" / "dashboard.html"


def _payload() -> dict:
    from payload import build_payload
    t0 = time.perf_counter()
    p = build_payload()
    p["wall_clock_s"] = round(time.perf_counter() - t0, 3)
    return p


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _route(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/", "/index.html"):
            if not WEB.exists():
                return self._send(500, b"web/dashboard.html missing", "text/plain")
            return self._send(200, WEB.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/health":
            return self._json({"ok": True, "runtime": "vercel",
                               "python": sys.version.split()[0]})
        if path in ("/api/run", "/api/state"):
            return self._json(_payload())
        self._send(404, b"not found", "text/plain")

    def do_GET(self):
        try:
            self._route()
        except Exception:
            self._json({"error": traceback.format_exc(limit=4)}, 500)

    def do_POST(self):
        self.do_GET()

    def log_message(self, fmt, *args):
        pass
