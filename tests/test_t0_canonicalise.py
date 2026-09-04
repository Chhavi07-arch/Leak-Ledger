"""T0 -- canonicalisation as a first-class tier.

Guards the property that made this worth extracting from detect_duplicate_capture:
rows in == rows canonical + rows collapsed, always, with every collapse recorded.
A silent dedupe is how export noise gets reported as customer harm.
"""
import csv, pathlib, sys, unittest
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import IST                                             # noqa: E402
from leakledger.money import Money                                          # noqa: E402
from leakledger.schema import GatewayPayment, ingest_rows                    # noqa: E402
from leakledger.cascade.engine import canonicalise                           # noqa: E402
from leakledger.leakage.detectors import detect_duplicate_capture            # noqa: E402
from leakledger.leakage.findings import FindingSet                           # noqa: E402

DATA = ROOT / "data" / "generated"


def _p(pid, order="ord_1", amt=100_00, hour=10, instrument="UPI", row=1):
    return GatewayPayment(payment_id=pid, order_id=order, rrn=None,
                          captured_at=datetime(2026, 6, 10, hour, 0, tzinfo=IST),
                          amount=Money(amt), instrument=instrument, bank=None,
                          is_international=False, status="CAPTURED",
                          fee_charged=Money(0), gst_charged=Money(0), row_num=row)


class Conservation(unittest.TestCase):
    def test_rows_in_equals_canonical_plus_collapsed(self):
        r = canonicalise([_p("a", row=1), _p("a", row=2), _p("b", row=3)])
        self.assertEqual(r.rows_in, 3)
        self.assertEqual(len(r.canonical) + len(r.collapsed), 3)

    def test_identical_repeat_is_collapsed(self):
        r = canonicalise([_p("a", row=1), _p("a", row=2)])
        self.assertEqual(len(r.canonical), 1)
        self.assertEqual(r.collapsed[0]["action"], "COLLAPSED")
        self.assertEqual(r.collapsed[0]["kept_row"], 1)
        self.assertEqual(r.collapsed[0]["row_num"], 2)

    def test_conflicting_repeat_is_KEPT_not_silently_dropped(self):
        """Same id, different amount, is a data conflict -- not a duplicate.
        Dropping it would discard evidence of a real problem."""
        r = canonicalise([_p("a", amt=100_00, row=1), _p("a", amt=250_00, row=2)])
        self.assertEqual(len(r.canonical), 2, "conflicting row was silently dropped")
        self.assertEqual(r.collapsed[0]["action"], "KEPT_CONFLICTING")

    def test_conflict_on_instrument_also_kept(self):
        r = canonicalise([_p("a", instrument="UPI", row=1),
                          _p("a", instrument="CREDIT_CARD", row=2)])
        self.assertEqual(len(r.canonical), 2)

    def test_nothing_to_do_is_a_no_op(self):
        r = canonicalise([_p("a"), _p("b"), _p("c")])
        self.assertEqual(len(r.canonical), 3)
        self.assertEqual(r.collapsed, [])


class RealBatch(unittest.TestCase):
    def test_t0_fires_on_the_generated_export(self):
        """The tier must actually do something on real data, not be theoretical."""
        with (DATA / "gateway_payments.csv").open(encoding="utf-8") as fh:
            gw = ingest_rows("gateway", list(csv.DictReader(fh)))
        r = canonicalise(gw.records)
        self.assertGreater(len(r.collapsed), 0,
                           "T0 collapsed nothing; the tier is unreachable on this batch")
        self.assertEqual(r.rows_in, len(gw.records))
        ids = [p.payment_id for p in r.canonical]
        self.assertEqual(len(ids), len(set(ids)), "canonical set still has repeats")


class DetectorContract(unittest.TestCase):
    def test_detector_rejects_non_canonical_input_loudly(self):
        """It used to dedupe silently. Now a caller that skips T0 must fail."""
        out = FindingSet()
        with self.assertRaises(ValueError) as cm:
            detect_duplicate_capture([_p("a", row=1), _p("a", row=2)], out)
        self.assertIn("T0-canonical", str(cm.exception))

    def test_detector_accepts_canonical_input(self):
        out = FindingSet()
        r = canonicalise([_p("a", row=1), _p("a", row=2)])
        detect_duplicate_capture(r.canonical, out)   # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
