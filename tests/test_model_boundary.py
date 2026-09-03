"""The AI-judgment boundary, tested with adversarial proposals.

PLAN.md's architecture claims a model PROPOSES and arithmetic DISPOSES. A claim
like that is only worth making if it is tested with proposals chosen to do
damage, rather than with whatever a live model happens to emit on a good day.
Every test here feeds a confidently-wrong, well-formed proposal and asserts it is
rejected.

No live model is required or used. That is deliberate: a boundary that depends on
the model behaving is not a boundary.
"""
import csv, json, pathlib, sys, unittest
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.money import Money                                            # noqa: E402
from leakledger.ai import narration, rationale, qa                            # noqa: E402
from leakledger.ai.provider import (                                          # noqa: E402
    AdversarialProvider, AnthropicProvider, ScriptedProvider, default_provider)


class NarrationBoundary(unittest.TestCase):
    """A proposal may only ever confirm arithmetic, never substitute for it."""

    def setUp(self):
        self.records = {"UTR20260001": Money(10_000_00), "UTR20260002": Money(25_000_00)}

    def test_confidently_wrong_reference_is_rejected(self):
        c = narration.Candidate("ACME PVT LTD", "UTR20260002", 0.99)
        v = narration.verify(c, expected_amount=Money(10_000_00),
                             records_by_reference=self.records)
        self.assertFalse(v.accepted)
        self.assertIn("arithmetic rejects", v.reason)

    def test_invented_reference_is_rejected(self):
        c = narration.Candidate("ACME PVT LTD", "UTR99999999", 1.0)
        v = narration.verify(c, expected_amount=Money(10_000_00),
                             records_by_reference=self.records)
        self.assertFalse(v.accepted)
        self.assertIn("does not exist", v.reason)

    def test_confidence_is_never_consulted(self):
        """A high-confidence wrong answer and a low-confidence right answer are
        treated identically. The model's estimate of itself is not evidence."""
        wrong_sure = narration.Candidate("X", "UTR20260002", 1.0)
        right_unsure = narration.Candidate("X", "UTR20260001", 0.01)
        self.assertFalse(narration.verify(
            wrong_sure, expected_amount=Money(10_000_00),
            records_by_reference=self.records).accepted)
        self.assertTrue(narration.verify(
            right_unsure, expected_amount=Money(10_000_00),
            records_by_reference=self.records).accepted)

    def test_off_by_one_paisa_is_rejected(self):
        """No tolerance band: a near miss is a miss."""
        c = narration.Candidate("X", "UTR20260001", 0.9)
        v = narration.verify(c, expected_amount=Money(10_000_01),
                             records_by_reference=self.records)
        self.assertFalse(v.accepted)

    def test_malformed_replies_are_rejected_not_repaired(self):
        for bad in ("", "not json", "{broken", "[]", "null",
                    "Sure! Here you go: no object at all"):
            self.assertIsNone(narration.parse_reply(bad), f"repaired: {bad!r}")

    def test_adversarial_provider_end_to_end_never_matches(self):
        """Full path: adversarial model -> parse -> verify. Nothing gets through."""
        prov = AdversarialProvider(counterparties=["ACME PVT LTD", "BHARAT RETAIL"],
                                   references=["UTR20260002", "UTR99999999"])
        accepted = 0
        for _ in range(50):
            cand = narration.propose(prov, "NEFT CR-UTR20260001-ACME PVT LTD")
            v = narration.verify(cand, expected_amount=Money(10_000_00),
                                 records_by_reference=self.records)
            accepted += v.accepted
        self.assertEqual(accepted, 0,
                         "an adversarial proposal was accepted; the boundary leaks")


class RationaleBoundary(unittest.TestCase):
    def test_fabricated_figure_is_discarded(self):
        """A rationale that invents a number falls back to deterministic evidence."""
        prov = ScriptedProvider(["The payout is short by Rs 9,999.99 and needs review."])
        text, provenance = rationale.render(
            prov, reason_code="PRIMARY_CYCLE_UNRECONCILED",
            evidence="cycle 2026-06-25 has payments but does not reconcile",
            next_action="confirm against merchant order IDs")
        self.assertEqual(provenance, "deterministic")
        self.assertNotIn("9,999.99", text)

    def test_faithful_rationale_is_kept(self):
        prov = ScriptedProvider(["Cycle 2026-06-25 has payments but does not reconcile; "
                                 "confirm against merchant order IDs."])
        text, provenance = rationale.render(
            prov, reason_code="PRIMARY_CYCLE_UNRECONCILED",
            evidence="cycle 2026-06-25 has payments but does not reconcile",
            next_action="confirm against merchant order IDs")
        self.assertEqual(provenance, "model")

    def test_model_failure_degrades_to_evidence_not_to_silence(self):
        class Failing:
            name = "failing"
            def complete(self, **kw):
                from leakledger.ai.provider import ModelReply
                return ModelReply(text="", error="APIConnectionError")
        text, provenance = rationale.render(
            Failing(), reason_code="X", evidence="deterministic evidence string",
            next_action="act")
        self.assertEqual(provenance, "deterministic")
        self.assertEqual(text, "deterministic evidence string")


class ProviderHonesty(unittest.TestCase):
    def test_no_silent_stub_substitution(self):
        """A missing credential must never resolve to a stub. A benchmark that
        quietly measured a stub would be worse than no benchmark."""
        import os
        had = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.assertIsNone(default_provider())
            with self.assertRaises(RuntimeError):
                default_provider(require_live=True)
        finally:
            if had is not None:
                os.environ["ANTHROPIC_API_KEY"] = had

    def test_qa_has_no_write_path(self):
        """Q&A accepts facts, not the engine. Enforced by signature."""
        import inspect
        params = set(inspect.signature(qa.ask).parameters)
        self.assertEqual(params, {"provider", "summary", "question"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
