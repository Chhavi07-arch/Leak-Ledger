import unittest, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from leakledger.schema import (
    BAD_AMOUNT, BAD_ENUM, BAD_TIMESTAMP, MISSING_FIELD,
    BankTransaction, ErpInvoice, GatewayPayment, QuarantinedRow, ingest_rows,
)

GOOD_GW = {
    "payment_id": "pay_1", "order_id": "ord_1", "rrn": "123456789012",
    "captured_at": "2026-06-10T23:58:00", "amount": "4500.00",
    "instrument": "DEBIT_CARD", "status": "CAPTURED",
    "fee_charged": "40.50", "gst_charged": "7.29",
}
GOOD_BANK = {
    "txn_id": "b1", "value_date": "2026-06-12", "amount": "184300.00",
    "direction": "CR", "utr": "UTR0001", "narration": "NEFT/UTR0001/ACME IND PVT LT",
}
GOOD_ERP = {
    "invoice_id": "inv_1", "issued_date": "2026-06-01", "amount": "5000.00",
    "counterparty": "Acme India Pvt Ltd", "status": "OPEN",
}


class TestNothingIsEverDropped(unittest.TestCase):
    """The core guard: rows in == records out + quarantined out. Always."""

    def test_counts_reconcile_gateway(self):
        rows = [GOOD_GW, {**GOOD_GW, "amount": "12.345"}, {**GOOD_GW, "instrument": "CRYPTO"}]
        r = ingest_rows("gateway", rows)
        self.assertEqual(r.total_rows, len(rows))
        self.assertEqual(len(r.records), 1)
        self.assertEqual(len(r.quarantined), 2)

    def test_counts_reconcile_all_bad(self):
        rows = [{}, {}, {}]
        r = ingest_rows("gateway", rows)
        self.assertEqual(r.total_rows, 3)
        self.assertEqual(len(r.records), 0)

    def test_row_numbers_preserved_for_traceback(self):
        r = ingest_rows("gateway", [{**GOOD_GW, "amount": "bad"}, GOOD_GW])
        self.assertEqual(r.quarantined[0].row_num, 1)
        self.assertEqual(r.records[0].row_num, 2)

    def test_raw_row_retained_on_quarantine(self):
        r = ingest_rows("gateway", [{**GOOD_GW, "instrument": "CRYPTO"}])
        self.assertEqual(r.quarantined[0].raw["instrument"], "CRYPTO")


class TestTypedQuarantineReasons(unittest.TestCase):
    """Every rejection carries a reason code — no untyped exits."""

    def test_missing_field(self):
        r = ingest_rows("gateway", [{k: v for k, v in GOOD_GW.items() if k != "order_id"}])
        self.assertEqual(r.quarantined[0].reason_code, MISSING_FIELD)

    def test_empty_string_counts_as_missing(self):
        r = ingest_rows("gateway", [{**GOOD_GW, "order_id": "   "}])
        self.assertEqual(r.quarantined[0].reason_code, MISSING_FIELD)

    def test_bad_amount(self):
        r = ingest_rows("gateway", [{**GOOD_GW, "amount": "not-money"}])
        self.assertEqual(r.quarantined[0].reason_code, BAD_AMOUNT)

    def test_sub_paise_amount_quarantined_not_rounded(self):
        r = ingest_rows("gateway", [{**GOOD_GW, "amount": "12.345"}])
        self.assertEqual(r.quarantined[0].reason_code, BAD_AMOUNT)
        self.assertIn("sub-paise", r.quarantined[0].detail)

    def test_bad_timestamp(self):
        r = ingest_rows("gateway", [{**GOOD_GW, "captured_at": "10/06/2026"}])
        self.assertEqual(r.quarantined[0].reason_code, BAD_TIMESTAMP)

    def test_bad_instrument_enum(self):
        r = ingest_rows("gateway", [{**GOOD_GW, "instrument": "CRYPTO"}])
        self.assertEqual(r.quarantined[0].reason_code, BAD_ENUM)

    def test_bad_status_enum(self):
        r = ingest_rows("gateway", [{**GOOD_GW, "status": "PENDING"}])
        self.assertEqual(r.quarantined[0].reason_code, BAD_ENUM)

    def test_bad_direction_enum(self):
        r = ingest_rows("bank", [{**GOOD_BANK, "direction": "SIDEWAYS"}])
        self.assertEqual(r.quarantined[0].reason_code, BAD_ENUM)

    def test_every_quarantine_has_a_code_and_detail(self):
        rows = [{}, {**GOOD_GW, "amount": "x"}, {**GOOD_GW, "instrument": "Z"},
                {**GOOD_GW, "captured_at": "z"}]
        for q in ingest_rows("gateway", rows).quarantined:
            self.assertTrue(q.reason_code)
            self.assertTrue(q.detail)


class TestParsedShapes(unittest.TestCase):
    def test_gateway_typed_correctly(self):
        rec = ingest_rows("gateway", [GOOD_GW]).records[0]
        self.assertIsInstance(rec, GatewayPayment)
        self.assertEqual(rec.amount.paise, 450000)
        self.assertEqual(rec.fee_charged.to_rupees_str(), "40.50")
        self.assertIsNotNone(rec.captured_at.tzinfo)

    def test_optional_fee_absent_is_none_not_zero(self):
        """Absent is not the same as zero; conflating them invents a leak."""
        rec = ingest_rows("gateway", [{k: v for k, v in GOOD_GW.items()
                                       if k not in ("fee_charged", "gst_charged")}]).records[0]
        self.assertIsNone(rec.fee_charged)
        self.assertIsNone(rec.gst_charged)

    def test_instrument_normalised_to_upper(self):
        rec = ingest_rows("gateway", [{**GOOD_GW, "instrument": "debit_card"}]).records[0]
        self.assertEqual(rec.instrument, "DEBIT_CARD")

    def test_international_flag_parsed(self):
        for truthy in ("true", "TRUE", "1", "yes"):
            rec = ingest_rows("gateway", [{**GOOD_GW, "is_international": truthy}]).records[0]
            self.assertTrue(rec.is_international)
        rec = ingest_rows("gateway", [GOOD_GW]).records[0]
        self.assertFalse(rec.is_international)

    def test_bank_row(self):
        rec = ingest_rows("bank", [GOOD_BANK]).records[0]
        self.assertIsInstance(rec, BankTransaction)
        self.assertEqual(rec.direction, "CR")
        self.assertIn("ACME", rec.narration)

    def test_erp_row(self):
        rec = ingest_rows("erp", [GOOD_ERP]).records[0]
        self.assertIsInstance(rec, ErpInvoice)
        self.assertEqual(rec.status, "OPEN")

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            ingest_rows("ledger", [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
