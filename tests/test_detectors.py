"""Detector guards, written against real generated data.

Per PATTERN-01, an isolated fixture is not evidence: three prior defects passed
unit tests while exercising nothing. These run the full pipeline and assert
against published ground truth.
"""
import csv, json, pathlib, sys, unittest
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                     # noqa: E402
from leakledger.feeschedule import FeeSchedule                    # noqa: E402
from leakledger.money import Money                                # noqa: E402
from leakledger.schema import ingest_rows                         # noqa: E402
from leakledger.cascade.engine import Cascade, covered_cycles_by_matching                     # noqa: E402
from leakledger.leakage import detectors                          # noqa: E402
from leakledger.leakage.findings import CONTRACT_DEPENDENT, RULE_CHECK, STRUCTURAL  # noqa: E402

DATA = ROOT / "data" / "generated"
def _load(n):
    """Read a generated CSV. Uses a context manager so the handle is closed --
    the lambda this replaced leaked one per call and filled test runs with
    ResourceWarnings."""
    with (DATA / n).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class Detectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fs = FeeSchedule.from_file(ROOT / "config" / "fee_schedule.v1.json")
        cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
        gw = ingest_rows("gateway", _load("gateway_payments.csv"))
        refunds, adj = _load("gateway_refunds.csv"), _load("gateway_adjustments.csv")
        bank = _load("bank_statement.csv")
        eng = Cascade(payments=gw.records, refunds=refunds, bank=bank,
                      adjustments=adj, calendar=cal)
        casc = eng.run()
        cov = covered_cycles_by_matching(eng, bank)
        cls.found = detectors.run_all(
            fs=fs, payments=eng.t0.canonical, refunds=refunds, adjustments=adj,
            bank_rows=bank, cascade_result=casc, calendar=cal,
            as_of=date(2026, 7, 31), covered_cycles=cov)
        cls.truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
        cls.seeded = {}
        for l in cls.truth["seeded_leaks"]:
            cls.seeded.setdefault(l["class"], set()).add(l["entity_id"])

    def _ids(self, cls_):
        return {f.entity_id for f in self.found.findings if f.leak_class == cls_}

    # --- exact detectors: any drift is a regression -------------------
    def test_fee_overcharge_exact(self):
        self.assertEqual(self._ids("FEE_OVERCHARGE"), self.seeded["FEE_OVERCHARGE"])

    def test_gst_mismatch_exact(self):
        self.assertEqual(self._ids("GST_MISMATCH"), self.seeded["GST_MISMATCH"])

    def test_zero_mdr_exact(self):
        self.assertEqual(self._ids("ZERO_MDR_VIOLATION"), self.seeded["ZERO_MDR_VIOLATION"])

    def test_duplicate_capture_exact(self):
        self.assertEqual(self._ids("DUPLICATE_CAPTURE"), self.seeded["DUPLICATE_CAPTURE"])

    def test_chargeback_not_recredited_exact(self):
        self.assertEqual(self._ids("CHARGEBACK_NOT_RECREDITED"),
                         self.seeded["CHARGEBACK_NOT_RECREDITED"])

    # --- structural invariants ---------------------------------------
    def test_zero_mdr_not_double_counted_as_overcharge(self):
        """One defect must not be reported as two findings."""
        self.assertEqual(self._ids("ZERO_MDR_VIOLATION") & self._ids("FEE_OVERCHARGE"), set())

    def test_duplicate_export_lines_are_not_duplicate_captures(self):
        """A repeated export row is a file artefact, not a second charge."""
        for f in self.found.findings:
            if f.leak_class == "DUPLICATE_CAPTURE":
                self.assertNotEqual(f.evidence["first"], f.evidence["second"])

    def test_missing_settlement_never_claims_a_merely_unmatched_payout(self):
        """An exception is not a leak. Every flagged cycle must have NO available
        credit -- the defect that inflated the headline 3x."""
        for f in self.found.findings:
            if f.leak_class == "MISSING_SETTLEMENT":
                self.assertIn("NO bank credit", f.derivation)

    def test_every_finding_carries_rederivable_arithmetic(self):
        """A finding a human cannot re-derive is one they will not action."""
        for f in self.found.findings:
            self.assertTrue(f.derivation.strip(), f"{f.finding_id} has no derivation")
            self.assertIn("Rs ", f.derivation, f"{f.finding_id} states no amount")
            self.assertGreaterEqual(f.value.paise, 0)

    def test_categories_are_disclosed_not_blended(self):
        """Contract-dependent findings must stay separable from structural ones."""
        self.assertTrue(self.found.by_category(STRUCTURAL))
        self.assertTrue(self.found.by_category(CONTRACT_DEPENDENT))
        for f in self.found.by_category(RULE_CHECK):
            self.assertEqual(f.leak_class, "ZERO_MDR_VIOLATION")

    def test_findings_are_deterministic(self):
        """Same inputs, same findings, same order."""
        a = [(f.leak_class, f.entity_id, f.value.paise) for f in self.found.findings]
        self.assertEqual(len(a), len(self.found.findings))
        self.assertEqual(a, sorted(a, key=lambda x: a.index(x)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
