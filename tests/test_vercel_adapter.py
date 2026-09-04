"""The serverless adapter must serve the same payload as the local server.

A deploy that quietly computes its own numbers would reintroduce exactly the
problem tests/test_dashboard.py exists to prevent, one layer further out.
"""
import json, pathlib, sys, threading, socketserver, unittest, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "harness"))


class VercelAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from index import handler
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def _get(self, path, method="GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method)
        if method == "POST":
            req.data = b""
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()

    def test_serves_the_dashboard_file_itself(self):
        code, body = self._get("/")
        self.assertEqual(code, 200)
        self.assertEqual(body, (ROOT / "web" / "dashboard.html").read_bytes())

    def test_api_run_matches_build_payload(self):
        from payload import build_payload
        code, body = self._get("/api/run", "POST")
        self.assertEqual(code, 200)
        served = json.loads(body)
        local = build_payload()
        for key in ("value", "false_match", "disposition"):
            self.assertEqual(served[key], json.loads(json.dumps(local[key], default=str)),
                             f"adapter disagrees with build_payload on {key}")

    def test_health_reports_the_runtime(self):
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_checks_survive_without_subprocess(self):
        """Serverless hosts may not allow spawning sys.executable; the in-process
        fallback must still really run the validator, not report a stub."""
        import subprocess
        from payload import build_payload
        real = subprocess.run
        subprocess.run = lambda *a, **k: (_ for _ in ()).throw(OSError("no spawn"))
        try:
            p = build_payload()
        finally:
            subprocess.run = real
        self.assertTrue(p["checks"]["ground_truth"]["ok"])
        self.assertEqual(p["checks"]["ground_truth"]["count"], 669)
        self.assertTrue(p["checks"]["model_boundary"]["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
