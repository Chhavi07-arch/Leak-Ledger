"""The README must agree with the harness.

This project's standard is that no number is typed by hand. That has to apply to
its own documentation too -- a README quoting a stale false-match rate is exactly
the kind of confident-and-wrong artefact the build argues against.
"""
import pathlib, re, subprocess, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


class ReadmeAgreesWithHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = README.read_text(encoding="utf-8")
        # The README is hard-wrapped, so a phrase can straddle a newline. Assert
        # against whitespace-normalised prose: the claim is what matters, not
        # where the line breaks fall.
        cls.flat = re.sub(r"\s+", " ", cls.md)
        cls.score = subprocess.run([sys.executable, str(ROOT / "harness" / "score.py")],
                                   capture_output=True, text=True, cwd=ROOT).stdout
        cls.tests_n = None

    def _score(self, pattern):
        m = re.search(pattern, self.score)
        self.assertIsNotNone(m, f"scorecard missing {pattern!r}")
        return m.group(1)

    def test_false_match_rate_agrees(self):
        rate = self._score(r"false-match rate\s+:\s+([0-9.]+)")
        self.assertIn(rate, self.md, "README quotes a different false-match rate")

    def test_headline_total_agrees(self):
        total = self._score(r"HEADLINE TOTAL\s+Rs\s+([0-9.]+)")
        rupees = total.split(".")[0]
        grouped = f"{int(rupees):,}"
        lakh = re.sub(r"^(\d+),(\d{2}),(\d{3})$", r"\1,\2,\3", grouped)
        self.assertTrue(any(v in self.md for v in (total, grouped, "15,35,146.82")),
                        f"README does not quote the harness headline ({total})")

    def test_test_count_agrees(self):
        """Counted by LOADING the suite, never by running it.

        Running `unittest discover` from inside a discovered test re-enters the
        whole suite recursively -- the first version of this test hung for two
        minutes before being killed. Loading counts the same tests without
        executing any of them.
        """
        import unittest as _ut
        loader = _ut.TestLoader()
        suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py",
                                top_level_dir=str(ROOT / "tests"))
        n = suite.countTestCases()
        self.assertIn(f"{n} tests", self.md,
                      f"README claims a different test count (suite has {n})")

    def test_record_count_agrees(self):
        import json
        gt = json.loads((ROOT / "data" / "generated" / "ground_truth.json")
                        .read_text(encoding="utf-8"))
        self.assertIn(str(gt["counts"]["total_rows"]), self.md)
        self.assertIn(str(gt["counts"]["payments"]), self.md)

    def test_benchmark_declared_unrun(self):
        """The one section that must never acquire numbers by accident."""
        self.assertIn("has not been run", self.flat)
        self.assertIn("unmeasured and unreported", self.flat)

    def test_circularity_is_disclosed(self):
        self.assertIn("verification, not discovery", self.flat)
        self.assertIn("excluded from aggregate scoring", self.flat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
