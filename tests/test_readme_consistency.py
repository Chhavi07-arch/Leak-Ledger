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

    def test_headline_reports_confirmed_not_gross(self):
        """The README's headline must lead with CONFIRMED value, not the gross.

        Guards INC-017: 69% of the gross figure comes from classes whose measured
        precision is below 1.00. A headline quoting the gross alone overstates
        confidence, which is the INC-010 failure with better packaging.
        """
        confirmed = self._score(r"CONFIRMED\s+Rs\s+([0-9.]+)")
        flagged = self._score(r"FLAGGED, not confirmed\s+Rs\s+([0-9.]+)")

        def lakh(v):                      # 474517.27 -> 4,74,517.27
            w, _, f = v.partition(".")
            head, tail = w[:-3], w[-3:]
            groups = []
            while len(head) > 2:
                groups.insert(0, head[-2:]); head = head[:-2]
            if head:
                groups.insert(0, head)
            return ",".join(groups + [tail]) + ("." + f if f else "")

        self.assertIn(lakh(confirmed), self.flat,
                      f"README does not quote the CONFIRMED figure ({lakh(confirmed)})")
        self.assertIn(lakh(flagged), self.flat,
                      f"README does not quote the FLAGGED figure ({lakh(flagged)})")
        # the word CONFIRMED must appear before the gross figure in the headline
        head = self.flat[:1200]
        self.assertIn("CONFIRMED", head,
                      "README headline does not distinguish confirmed from flagged")

    def test_weak_classes_are_named_with_their_precision(self):
        """A reader must not have to infer which detectors are unreliable."""
        for cls, prec in (("MISSING_SETTLEMENT", "0.20"), ("RESERVE_NOT_RELEASED", "0.67")):
            self.assertIn(cls, self.flat)
            self.assertIn(prec, self.flat,
                          f"README names {cls} without its measured precision {prec}")

    def test_value_convention_is_stated(self):
        """Whether 'value' means all-found or only-verified must be explicit."""
        self.assertIn("sums every instance the engine reported", self.flat.lower())

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
