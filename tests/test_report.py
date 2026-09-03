"""The report must agree with the harness and must not overstate.

Two failure modes worth guarding: a surface that quietly disagrees with the
scorecard (two sources of truth), and a surface that presents an unrun benchmark
as if it had numbers.
"""
import pathlib, re, subprocess, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "run_report.html"


class Report(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, str(ROOT / "harness" / "report.py")],
                       check=True, capture_output=True, cwd=ROOT)
        cls.html = REPORT.read_text(encoding="utf-8")

    def test_refusals_show_competing_answers_not_a_count(self):
        """The reason the surface exists: a refusal must be inspectable."""
        self.assertIn('class="refusal"', self.html)
        self.assertGreaterEqual(self.html.count('class="cand"'), 4,
                                "fewer than two candidates rendered per refusal")
        self.assertIn('class="diff"', self.html,
                      "the differing element between candidates is not highlighted")

    def test_false_match_rate_matches_the_harness(self):
        out = subprocess.run([sys.executable, str(ROOT / "harness" / "score.py")],
                             capture_output=True, text=True, cwd=ROOT).stdout
        m = re.search(r"false-match rate\s+:\s+([0-9.]+)", out)
        self.assertIsNotNone(m, "scorecard did not report a false-match rate")
        self.assertIn(m.group(1), self.html,
                      "report and scorecard disagree on the primary metric")

    def test_unrun_benchmark_is_declared_not_implied(self):
        """No invented numbers standing in for a measurement that did not happen."""
        self.assertIn("has not been run", self.html)
        self.assertIn("unmeasured and unreported", self.html)

    def test_contract_dependent_findings_are_labelled(self):
        self.assertIn("verification, not discovery", self.html)

    def test_deterministic_output_apart_from_timestamp(self):
        first = re.sub(r"generated \d{4}-\d\d-\d\d \d\d:\d\d", "", self.html)
        subprocess.run([sys.executable, str(ROOT / "harness" / "report.py")],
                       check=True, capture_output=True, cwd=ROOT)
        second = re.sub(r"generated \d{4}-\d\d-\d\d \d\d:\d\d", "",
                        REPORT.read_text(encoding="utf-8"))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
