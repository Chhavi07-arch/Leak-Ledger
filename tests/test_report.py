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

    def test_benchmark_results_come_from_the_measured_artefact(self):
        """The benchmark table must be rendered from reports/benchmark_llm_matcher.json,
        never typed. Guards against the table drifting from the measurement."""
        import json
        bm = json.loads((ROOT / "reports" / "benchmark_llm_matcher.json")
                        .read_text(encoding="utf-8"))
        self.assertIn(bm["model"], self.html)
        self.assertIn(f'{bm["self_disagreement_records"]}/{bm["records"]}', self.html)
        self.assertIn(f'{bm["input_tokens"]:,}', self.html)
        self.assertIn(bm["selection_sha256"][:12], self.html,
                      "report does not cite the frozen selection hash")

    def test_uncomputed_cost_is_declared_not_implied(self):
        """USD was not computed because no verified price exists in-repo. That must
        be stated, not left as an absence a reader could mistake for zero."""
        self.assertIn("cost in USD is not computed", self.html.replace("&nbsp;", " "))
        self.assertIn("NOT computed rather than estimated", self.html)

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


class BenchmarkDefensibility(unittest.TestCase):
    """The benchmark must be checkable by a sceptic, not just correct.

    Three properties a panelist would probe: was the task fair, was determinism
    actually tested or merely configured, and can the prompt be inspected.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = REPORT.read_text(encoding="utf-8")

    def test_task_shape_expectation_is_stated(self):
        """A foreseeable result reported without saying so reads as cherry-picking."""
        self.assertIn("structurally weak", self.html)
        self.assertIn("subset-sum", self.html)
        self.assertIn("not the claim", self.html)

    def test_both_temperature_arms_are_reported(self):
        import json
        b0 = json.loads((ROOT / "reports" / "benchmark_llm_matcher_temp0.json")
                        .read_text(encoding="utf-8"))
        self.assertEqual(b0["temperature_sent"], 0.0)
        self.assertIn("temperature = 0", self.html)
        self.assertIn(f'{b0["self_disagreement_records"]}/{b0["records"]}', self.html)
        self.assertIn("did not produce determinism", self.html)

    def test_prompt_is_reproduced_and_not_transcribed(self):
        """The report renders the benchmark's own SYSTEM constant, so the two
        cannot drift; a transcribed copy could quietly diverge."""
        import importlib.util as ilu
        spec = ilu.spec_from_file_location(
            "_bench_t", ROOT / "harness" / "benchmark_llm_matcher.py")
        mod = ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        import html as _h
        self.assertIn(_h.escape(mod.SYSTEM), self.html,
                      "report does not reproduce the benchmark's actual system prompt")
