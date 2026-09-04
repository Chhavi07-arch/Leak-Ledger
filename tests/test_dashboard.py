"""The dashboard must agree with the harness, and must not become a second
source of truth.

A surface that recomputes its own numbers will eventually disagree with the
scorecard, and the first time it does the whole submission loses its footing.
These tests assert the payload the dashboard renders is the same one the
scorecard reads.
"""
import json, pathlib, re, subprocess, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))


class Payload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from payload import build_payload
        cls.p = build_payload()
        cls.score = subprocess.run([sys.executable, str(ROOT / "harness" / "score.py")],
                                   capture_output=True, text=True, cwd=ROOT).stdout

    def test_false_match_rate_matches_scorecard(self):
        m = re.search(r"false-match rate\s+:\s+([0-9.]+)", self.score)
        self.assertIsNotNone(m)
        self.assertAlmostEqual(float(m.group(1)), self.p["false_match"]["rate"], places=4)

    def test_confirmed_and_flagged_match_scorecard(self):
        c = re.search(r"CONFIRMED\s+Rs\s+([0-9.]+)", self.score).group(1)
        f = re.search(r"FLAGGED, not confirmed\s+Rs\s+([0-9.]+)", self.score).group(1)
        self.assertEqual(c, self.p["value"]["confirmed"])
        self.assertEqual(f, self.p["value"]["flagged"])

    def test_every_finding_class_carries_its_precision(self):
        """A class shown without precision is how INC-017 happened."""
        for f in self.p["findings"]:
            if f["category"] == "exception-signal":
                continue
            self.assertIsNotNone(f["precision"], f"{f['leak_class']} has no precision")
            self.assertIsNotNone(f["value_verified"], f"{f['leak_class']} has no verified value")

    def test_refusals_carry_their_competing_answers(self):
        self.assertGreater(len(self.p["refusals"]), 0)
        for r in self.p["refusals"]:
            self.assertGreaterEqual(len(r["candidates"]), 2,
                                    f"{r['bank_txn_id']} refusal shows fewer than 2 candidates")

    def test_payload_is_json_serialisable(self):
        json.dumps(self.p, default=str)

    def test_dashboard_html_exists_and_calls_the_api(self):
        html = (ROOT / "web" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("/api/run", html)
        self.assertIn("/api/state", html)
        self.assertNotIn("Math.random", html, "dashboard must not fabricate any value")


if __name__ == "__main__":
    unittest.main(verbosity=2)
