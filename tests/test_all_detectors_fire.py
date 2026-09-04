"""Permanent CI guard for PATTERN-01.

Every registered detector must fire at least once on the generated batch, and
every seeded case type must be exercised. This is the test that would have caught
INC-011 the moment it was written (a detector keyed on a reason code the engine
never emits), and in an earlier form it would have caught INC-004 and INC-009
(seeded cases present in ground truth that nothing could ever reach).

The rule it enforces: **a detector or seeded case is not evidence until it has
been observed to fire on real data.** A clean import and a non-zero count in
ground truth are neither.

If a detector genuinely cannot fire, that is a scope limitation and must be
recorded in UNDETECTABLE below with a reason -- explicitly, in code review, not
by silently scoring zero.
"""
import csv, json, pathlib, sys, unittest
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                     # noqa: E402
from leakledger.feeschedule import FeeSchedule                    # noqa: E402
from leakledger.schema import ingest_rows                         # noqa: E402
from leakledger.cascade.engine import Cascade, covered_cycles_by_matching                     # noqa: E402
from leakledger.leakage import detectors                          # noqa: E402
from leakledger.leakage.findings import CATEGORY                  # noqa: E402

DATA = ROOT / "data" / "generated"
def _load(n):
    """Read a generated CSV. Uses a context manager so the handle is closed --
    the lambda this replaced leaked one per call and filled test runs with
    ResourceWarnings."""
    with (DATA / n).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

# Detectors that provably cannot fire with payments + bank data alone.
# Each entry must carry a measured reason, not an intention.
UNDETECTABLE = {
    # (empty: every registered detector is expected to fire)
}

# Cascade reason codes that are defensive branches, unreached on THIS batch.
# Each needs a measured reason. Listing them here forces the decision into review
# rather than letting a permanently-dead exit pass unnoticed (INC-011).
UNREACHED_REASON_CODES = {
    # no two refunds share an amount inside one date window in this batch
    "AMBIGUOUS_CANDIDATE",
    # every bank credit has at least one candidate cycle with a non-empty pool
    "NO_RECONCILING_SET",
    # meet-in-the-middle indexing costs ~55k combinations at the largest pool,
    # far below the 5,000,000 budget; reachable only on far larger inputs
    "SEARCH_BUDGET_EXCEEDED",
}


class EveryDetectorFires(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fs = FeeSchedule.from_file(ROOT / "config" / "fee_schedule.v1.json")
        cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
        gw = ingest_rows("gateway", _load("gateway_payments.csv"))
        refunds, adj = _load("gateway_refunds.csv"), _load("gateway_adjustments.csv")
        bank = _load("bank_statement.csv")
        eng = Cascade(payments=gw.records, refunds=refunds, bank=bank,
                      adjustments=adj, calendar=cal)
        cls.casc = eng.run()
        cov = covered_cycles_by_matching(eng, bank)
        cls.found = detectors.run_all(
            fs=fs, payments=eng.t0.canonical, refunds=refunds, adjustments=adj,
            bank_rows=bank, cascade_result=cls.casc, calendar=cal,
            as_of=date(2026, 7, 31), covered_cycles=cov)
        cls.truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))

    def test_every_registered_detector_fires(self):
        fired = {f.leak_class for f in self.found.findings}
        expected = set(CATEGORY) - set(UNDETECTABLE)
        silent = sorted(expected - fired)
        self.assertEqual(
            silent, [],
            f"registered but never fired on the batch: {silent}. Either the "
            f"detector keys on a signal the engine does not emit (INC-011), or "
            f"the case is undetectable and belongs in UNDETECTABLE with a "
            f"measured reason.")

    def test_every_seeded_leak_class_is_exercised(self):
        seeded = {l["class"] for l in self.truth["seeded_leaks"]}
        fired = {f.leak_class for f in self.found.findings}
        self.assertEqual(sorted(seeded - fired - set(UNDETECTABLE)), [],
                         "seeded leak classes that no detector reported")

    def test_every_adversarial_case_type_is_reachable(self):
        """Guards INC-004/INC-009: a seeded adversarial case must affect outcomes."""
        types = {a["type"] for a in self.truth["adversarial_cases"]}
        self.assertGreaterEqual(len(types), 7,
                                "adversarial coverage shrank below 7 distinct types")
        traps = [a for a in self.truth["adversarial_cases"]
                 if a["type"] == "AMBIGUITY_TRAP"]
        links = self.truth["payment_settlement_links"]
        stl = {s["settlement_id"]: s for s in self.truth["settlements"]}
        for a in traps:
            hosts = {links.get(i) for i in a["entity_ids"]} - {None}
            self.assertTrue(
                any(stl[h]["paid"] for h in hosts if h in stl),
                f"{a['case_id']} sits entirely on unpaid settlements and can never fire")

    def test_every_cascade_reason_code_is_reachable(self):
        """The cascade's typed exits must not include codes nothing can produce."""
        from leakledger.cascade import engine as eng_mod
        declared = {v for k, v in vars(eng_mod).items()
                    if k.isupper() and isinstance(v, str) and k.endswith(
                        ("_EXCEEDED", "_SUBSET", "_FOUND", "_SET", "_RESIDUAL", "_CANDIDATE"))}
        emitted = {m.reason_code for m in self.casc.matches if m.reason_code}
        unreachable = sorted(declared - emitted - UNREACHED_REASON_CODES)
        self.assertEqual(
            unreachable, [],
            f"declared reason codes never emitted: {unreachable}. A detector "
            f"keyed on one of these can never fire (INC-011).")


if __name__ == "__main__":
    unittest.main(verbosity=2)
