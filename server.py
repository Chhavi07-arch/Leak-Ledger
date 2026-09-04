#!/usr/bin/env python3
"""Local dashboard server for Leak Ledger.

    python3 server.py            # then open http://localhost:8000

Standard library only -- no Flask, no FastAPI, no npm. The README claims zero
dependencies for the deterministic core and that claim is worth more than the
convenience of a framework.

The server does NOT compute anything itself. Every number it serves comes from
harness/payload.py, which is the same code the CLI scorecard and the static HTML
report read from. A dashboard that recomputed its own figures would be a second
source of truth, and the first time it drifted from the scorecard the whole
submission would lose its footing.

Endpoints
    GET  /                 the dashboard
    POST /api/run          run the full pipeline now, return the result
    GET  /api/state        the last run (runs one if none yet)
    GET  /api/health       liveness + provenance
"""
from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "harness"))
from payload import build_payload                                            # noqa: E402

WEB = ROOT / "web" / "dashboard.html"
_lock = threading.Lock()
_cache: dict = {}


def run_pipeline() -> dict:
    """Serialised: two concurrent runs would interleave timing measurements."""
    with _lock:
        t0 = time.perf_counter()
        p = build_payload()
        p["wall_clock_s"] = round(time.perf_counter() - t0, 3)
        _cache["last"] = p
        return p


class Handler(BaseHTTPRequestHandler):
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

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                if not WEB.exists():
                    return self._send(500, b"web/dashboard.html missing", "text/plain")
                return self._send(200, WEB.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/health":
                return self._json({"ok": True, "has_run": "last" in _cache})
            if self.path == "/api/state":
                return self._json(_cache.get("last") or run_pipeline())
            self._send(404, b"not found", "text/plain")
        except Exception:
            traceback.print_exc()
            self._json({"error": traceback.format_exc(limit=3)}, 500)

    def do_POST(self):
        try:
            if self.path == "/api/run":
                return self._json(run_pipeline())
            self._send(404, b"not found", "text/plain")
        except Exception:
            traceback.print_exc()
            self._json({"error": traceback.format_exc(limit=3)}, 500)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.command} {self.path}\n")


def main(port: int = 8000) -> int:
    print("Leak Ledger — local dashboard")
    print(f"  serving  http://localhost:{port}")
    print("  the server computes nothing itself; every figure comes from")
    print("  harness/payload.py, the same source the CLI scorecard reads.")
    print("  Ctrl-C to stop.\n")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    try:
        raise SystemExit(main(port))
    except KeyboardInterrupt:
        print("\nstopped")
