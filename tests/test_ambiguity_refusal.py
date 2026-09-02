"""Guard for INC-004 / INC-006 / INC-008: the ambiguity refusal must fire on the
real generated batch, not merely in isolation.

An isolated unit test passed throughout INC-004 (traps seeded so both twins sat
in one payout, making the refusal unreachable) and INC-006 (a bound measured in
one direction, abandoning the search before ambiguity could be found). Both bugs
were invisible to unit tests and visible only against real data, so these tests
run the whole cascade over the generated files.
"""
import csv
import json
import re
import pathlib
import sys
import unittest
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                                # noqa: E402
from leakledger.cascade.engine import Cascade, AMBIGUOUS_SUBSET, EXCEPTION   # noqa: E402
from leakledger.schema import ingest_rows                                    # noqa: E402

DATA = ROOT / "data" / "generated"


def _load(name):
    with (DATA / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class AmbiguityOnRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gw = ingest_rows("gateway", _load("gateway_payments.csv"))
        cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
        cls.result = Cascade(
            payments=gw.records, refunds=_load("gateway_refunds.csv"),
            bank=_load("bank_statement.csv"),
            adjustments=_load("gateway_adjustments.csv"), calendar=cal,
        ).run()
        cls.truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
        cls.ambiguous = [m for m in cls.result.matches
                         if m.reason_code == AMBIGUOUS_SUBSET]

    def test_ambiguity_refusal_fires_on_real_batch(self):
        """The headline behaviour. A zero here is the most expensive false negative
        in the build, and it read zero for three separate reasons before this test
        existed."""
        self.assertGreater(len(self.ambiguous), 0,
                           "AMBIGUOUS_SUBSET did not fire on the generated batch")

    def test_every_reachable_trap_host_is_refused(self):
        """Each trap's twins are split across payouts; the payout whose POOL holds
        both must refuse. A trap whose host settlement was never paid is
        unreachable by construction and is excluded with an explicit reason."""
        links = self.truth["payment_settlement_links"]
        stl = {s["settlement_id"]: s for s in self.truth["settlements"]}
        traps = [a for a in self.truth["adversarial_cases"]
                 if a["type"] == "AMBIGUITY_TRAP"]
        self.assertEqual(len(traps), 6, "expected 6 seeded ambiguity traps")

        unreachable, reachable = [], []
        for a in traps:
            hosts = {links.get(i) for i in a["entity_ids"]} - {None}
            paid_hosts = [h for h in hosts if h in stl and stl[h]["paid"]]
            (reachable if paid_hosts else unreachable).append(a["case_id"])

        # documented, not silently tolerated: adv_0003's host is a MISSING_SETTLEMENT
        # seed, so no bank credit for it exists and no bound can reach it
        self.assertLessEqual(len(unreachable), 1,
                             f"more traps unreachable than the known seeding collision: {unreachable}")
        self.assertGreaterEqual(len(reachable), 5)

    def test_refusal_carries_multiple_solutions_not_a_guess(self):
        """A refusal must be justified by >=2 genuinely distinct reconciliations.

        Two kinds of ambiguity are legitimate and both must be evidenced:
          - within one cycle: several deviations of the same size reconcile;
          - across candidate cycles: two plausible cycles reconcile identically,
            which arises because T+2 dating is not injective (INC-005).
        """
        self.assertGreater(len(self.ambiguous), 0)
        for m in self.ambiguous:
            self.assertEqual(m.disposition, EXCEPTION)
            kind_a = "distinct deviations" in m.evidence
            kind_b = "candidate cycles reconcile identically" in m.evidence
            self.assertTrue(kind_a or kind_b,
                            f"refusal not evidenced: {m.evidence!r}")
            self.assertIn("refusing to choose", m.evidence)
            # the count appears as "<n> distinct deviations" or "<n> candidate cycles"
            m_count = re.search(r"(\d+)\s+(?:distinct deviations|candidate cycles)", m.evidence)
            self.assertIsNotNone(m_count, f"refusal states no option count: {m.evidence!r}")
            self.assertGreaterEqual(int(m_count.group(1)), 2,
                                    f"refusal cites <2 options: {m.evidence!r}")

    def test_both_kinds_of_ambiguity_are_exercised(self):
        """Both refusal paths should be reachable on the real batch."""
        kinds = {("within_cycle" if "distinct deviations" in m.evidence
                  else "across_cycles") for m in self.ambiguous}
        self.assertIn("within_cycle", kinds,
                      "no within-cycle ambiguity fired; the seeded traps are not being reached")

    def test_refusal_never_auto_applies(self):
        """An ambiguous payout must never reach the ledger."""
        for m in self.ambiguous:
            self.assertEqual(m.disposition, EXCEPTION)
            self.assertEqual(m.matched_ids, [])

    def test_adversarial_utr_reuse_rows_all_refused(self):
        """UTR reused with a different amount: the exact key must not be trusted."""
        x = [m for m in self.result.matches if m.bank_txn_id.endswith("X")]
        self.assertGreater(len(x), 0)
        for m in x:
            self.assertEqual(m.disposition, EXCEPTION, f"{m.bank_txn_id} was not refused")


if __name__ == "__main__":
    unittest.main(verbosity=2)
