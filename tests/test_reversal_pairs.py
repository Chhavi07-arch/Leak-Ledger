"""REVERSAL_PAIR — legs that cancel out are not money movement.

The case: a bank posts an entry in error and reverses it. Same reference, same
amount, opposite direction, a day or two apart, netting to zero.

The failure this prevents is specific and costly: treated as two independent
rows, the pair manufactures a phantom credit the engine will try to reconcile
against a real cycle, plus an unexplained debit — two spurious exceptions per
pair out of nothing but the bank's own correction.
"""
import csv, json, pathlib, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                                # noqa: E402
from leakledger.schema import ingest_rows                                    # noqa: E402
from leakledger.cascade.engine import (                                      # noqa: E402
    EXCEPTION, Cascade, neutralise_reversals)

DATA = ROOT / "data" / "generated"


def _load(n):
    with (DATA / n).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _row(txn, amount, direction, utr, date):
    return {"txn_id": txn, "value_date": date, "amount": amount,
            "direction": direction, "utr": utr, "narration": ""}


class Unit(unittest.TestCase):
    def test_matching_pair_is_neutralised(self):
        rows = [_row("A", "500.00", "CR", "U1", "10-06-2026"),
                _row("B", "500.00", "DR", "U1", "11-06-2026")]
        canon, pairs = neutralise_reversals(rows)
        self.assertEqual(canon, [])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["days_apart"], 1)

    def test_same_direction_is_not_a_reversal(self):
        rows = [_row("A", "500.00", "CR", "U1", "10-06-2026"),
                _row("B", "500.00", "CR", "U1", "11-06-2026")]
        canon, pairs = neutralise_reversals(rows)
        self.assertEqual(pairs, [])
        self.assertEqual(len(canon), 2)

    def test_different_amount_is_not_a_reversal(self):
        rows = [_row("A", "500.00", "CR", "U1", "10-06-2026"),
                _row("B", "501.00", "DR", "U1", "11-06-2026")]
        canon, pairs = neutralise_reversals(rows)
        self.assertEqual(pairs, [])

    def test_reference_reused_far_apart_is_not_a_reversal(self):
        """A reference reused months later is not a correction."""
        rows = [_row("A", "500.00", "CR", "U1", "10-06-2026"),
                _row("B", "500.00", "DR", "U1", "10-09-2026")]
        canon, pairs = neutralise_reversals(rows)
        self.assertEqual(pairs, [], "a far-apart reuse was wrongly treated as a reversal")
        self.assertEqual(len(canon), 2)

    def test_no_reference_is_never_paired(self):
        rows = [_row("A", "500.00", "CR", "", "10-06-2026"),
                _row("B", "500.00", "DR", "", "11-06-2026")]
        canon, pairs = neutralise_reversals(rows)
        self.assertEqual(pairs, [])

    def test_conservation(self):
        rows = [_row("A", "500.00", "CR", "U1", "10-06-2026"),
                _row("B", "500.00", "DR", "U1", "11-06-2026"),
                _row("C", "900.00", "CR", "U2", "12-06-2026")]
        canon, pairs = neutralise_reversals(rows)
        self.assertEqual(len(canon) + 2 * len(pairs), len(rows))


class RealBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = _load("bank_statement.csv")
        cls.truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
        gw = ingest_rows("gateway", _load("gateway_payments.csv"))
        cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
        cls.eng = Cascade(payments=gw.records, refunds=_load("gateway_refunds.csv"),
                          bank=cls.bank, adjustments=_load("gateway_adjustments.csv"),
                          calendar=cal)
        cls.res = cls.eng.run()

    def test_every_seeded_pair_is_found(self):
        seeded = {r["utr"] for r in self.truth["reversal_pairs"]}
        self.assertGreater(len(seeded), 0, "no reversal pairs seeded — the case is unreachable")
        found = {p["reference"] for p in self.eng.t0.reversals}
        self.assertEqual(seeded - found, set(),
                         f"seeded reversal pairs the engine missed: {sorted(seeded - found)}")

    def test_neutralised_legs_never_reach_matching(self):
        """Neither leg may appear as a match or an exception — they are not events."""
        gone = set()
        for r in self.truth["reversal_pairs"]:
            gone.update({r["credit_txn_id"], r["debit_txn_id"]})
        seen = {m.bank_txn_id for m in self.res.matches}
        self.assertEqual(gone & seen, set(),
                         f"reversal legs reached the cascade: {sorted(gone & seen)}")

    def test_without_neutralisation_they_would_cause_spurious_exceptions(self):
        """Proves the tier earns its place rather than being decorative."""
        cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
        gw = ingest_rows("gateway", _load("gateway_payments.csv"))
        eng = Cascade(payments=gw.records, refunds=_load("gateway_refunds.csv"),
                      bank=self.bank, adjustments=_load("gateway_adjustments.csv"),
                      calendar=cal)
        eng.t0.bank_canonical = self.bank          # defeat the tier
        eng.t0.reversals = []
        eng.bank = self.bank
        res = eng.run()
        legs = set()
        for r in self.truth["reversal_pairs"]:
            legs.update({r["credit_txn_id"], r["debit_txn_id"]})
        spurious = [m for m in res.matches if m.bank_txn_id in legs]
        self.assertEqual(len(spurious), len(legs),
                         "expected every leg to surface when the tier is defeated")
        self.assertTrue(all(m.disposition == EXCEPTION for m in spurious),
                        "legs should become exceptions without neutralisation")

    def test_conservation_on_the_real_batch(self):
        t0 = self.eng.t0
        self.assertEqual(len(t0.bank_canonical) + 2 * len(t0.reversals), len(self.bank))


if __name__ == "__main__":
    unittest.main(verbosity=2)
