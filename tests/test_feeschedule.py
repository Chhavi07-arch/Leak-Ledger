import unittest, sys, pathlib, json, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from leakledger.money import Money
from leakledger.feeschedule import FeeSchedule, FeeScheduleError

CONFIG = pathlib.Path(__file__).resolve().parents[1] / "config" / "fee_schedule.v1.json"


class TestVersioningAndHash(unittest.TestCase):
    def setUp(self):
        self.fs = FeeSchedule.from_file(CONFIG)

    def test_version_present(self):
        self.assertEqual(self.fs.version, "fee_schedule.v1")

    def test_hash_is_stable_across_loads(self):
        self.assertEqual(FeeSchedule.from_file(CONFIG).sha256, self.fs.sha256)

    def test_hash_changes_when_file_changes(self):
        """Provenance guard: an edited contract must not reuse the old hash."""
        data = json.loads(CONFIG.read_bytes())
        data["instruments"]["CREDIT_CARD"]["slabs"][0]["bps"] = 210
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(data, fh)
            tmp = fh.name
        try:
            self.assertNotEqual(FeeSchedule.from_file(pathlib.Path(tmp)).sha256, self.fs.sha256)
        finally:
            os.unlink(tmp)

    def test_computation_carries_hash_and_version(self):
        c = self.fs.expected_fee("DEBIT_CARD", Money.from_rupees_str("4500.00"))
        self.assertEqual(c.schedule_sha256, self.fs.sha256)
        self.assertEqual(c.schedule_version, "fee_schedule.v1")


class TestSlabSelection(unittest.TestCase):
    def setUp(self):
        self.fs = FeeSchedule.from_file(CONFIG)

    def _fee(self, instrument, rupees, **kw):
        return self.fs.expected_fee(instrument, Money.from_rupees_str(rupees), **kw)

    def test_debit_below_boundary(self):
        c = self._fee("DEBIT_CARD", "1999.00")
        self.assertEqual(c.slab_label, "DEBIT_LTE_2000")
        self.assertEqual(c.rate_bps, 40)

    def test_debit_exactly_at_boundary_is_inclusive(self):
        """Documented convention: Rs 2000.00 takes the lower slab."""
        c = self._fee("DEBIT_CARD", "2000.00")
        self.assertEqual(c.slab_label, "DEBIT_LTE_2000")
        self.assertEqual(c.rate_bps, 40)

    def test_debit_one_paisa_above_boundary(self):
        c = self._fee("DEBIT_CARD", "2000.01")
        self.assertEqual(c.slab_label, "DEBIT_GT_2000")
        self.assertEqual(c.rate_bps, 90)

    def test_international_uses_international_slab(self):
        dom = self._fee("CREDIT_CARD", "1000.00")
        intl = self._fee("CREDIT_CARD", "1000.00", is_international=True)
        self.assertEqual(dom.rate_bps, 200)
        self.assertEqual(intl.rate_bps, 300)
        self.assertNotEqual(dom.fee, intl.fee)

    def test_upi_is_zero_mdr(self):
        c = self._fee("UPI", "50000.00")
        self.assertEqual(c.fee.paise, 0)
        self.assertEqual(c.gst.paise, 0)


class TestNetbankingFlat(unittest.TestCase):
    def setUp(self):
        self.fs = FeeSchedule.from_file(CONFIG)

    def test_flat_fee_does_not_scale_with_amount(self):
        """Percentage-only logic would silently mis-flag every netbanking row."""
        small = self.fs.expected_fee("NETBANKING", Money.from_rupees_str("100.00"), bank="SBI")
        large = self.fs.expected_fee("NETBANKING", Money.from_rupees_str("100000.00"), bank="SBI")
        self.assertEqual(small.fee, large.fee)
        self.assertEqual(small.fee.to_rupees_str(), "18.00")

    def test_per_bank_rates_differ(self):
        hdfc = self.fs.expected_fee("NETBANKING", Money.from_rupees_str("500.00"), bank="HDFC")
        sbi = self.fs.expected_fee("NETBANKING", Money.from_rupees_str("500.00"), bank="SBI")
        self.assertNotEqual(hdfc.fee, sbi.fee)

    def test_unknown_bank_uses_default_and_says_so(self):
        c = self.fs.expected_fee("NETBANKING", Money.from_rupees_str("500.00"), bank="NEWBANK")
        self.assertEqual(c.slab_label, "NETBANKING_DEFAULT")

    def test_missing_bank_is_an_error_not_a_silent_default(self):
        with self.assertRaises(FeeScheduleError):
            self.fs.expected_fee("NETBANKING", Money.from_rupees_str("500.00"))


class TestNeverGuess(unittest.TestCase):
    """An unknown instrument must raise, never be assigned a plausible rate.

    Guessing would manufacture a leakage finding with no contractual basis.
    """

    def setUp(self):
        self.fs = FeeSchedule.from_file(CONFIG)

    def test_unknown_instrument_raises(self):
        with self.assertRaises(FeeScheduleError):
            self.fs.expected_fee("AMEX_CORP", Money.from_rupees_str("500.00"))

    def test_bad_gst_policy_rejected_at_load(self):
        data = json.loads(CONFIG.read_bytes())
        data["gst_rounding_policy"] = "whatever"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(data, fh)
            tmp = fh.name
        try:
            with self.assertRaises(FeeScheduleError):
                FeeSchedule.from_file(pathlib.Path(tmp))
        finally:
            os.unlink(tmp)


class TestGstAndDerivation(unittest.TestCase):
    def setUp(self):
        self.fs = FeeSchedule.from_file(CONFIG)

    def test_gst_is_on_the_fee_not_the_transaction(self):
        """A classic inversion. GST of Rs 4500 would be Rs 810, not Rs 7.29."""
        c = self.fs.expected_fee("DEBIT_CARD", Money.from_rupees_str("4500.00"))
        self.assertEqual(c.fee.to_rupees_str(), "40.50")
        self.assertEqual(c.gst.to_rupees_str(), "7.29")
        self.assertNotEqual(c.gst.to_rupees_str(), "810.00")

    def test_total_deduction_is_fee_plus_gst(self):
        c = self.fs.expected_fee("DEBIT_CARD", Money.from_rupees_str("4500.00"))
        self.assertEqual(c.total_deduction, c.fee + c.gst)
        self.assertEqual(c.total_deduction.to_rupees_str(), "47.79")

    def test_derivation_is_human_rederivable(self):
        c = self.fs.expected_fee("DEBIT_CARD", Money.from_rupees_str("4500.00"))
        d = c.derivation()
        for fragment in ("DEBIT_GT_2000", "90 bps", "4500.00", "40.50", "7.29", "47.79"):
            self.assertIn(fragment, d)

    def test_composite_policy_available_and_may_differ(self):
        data = json.loads(CONFIG.read_bytes())
        data["gst_rounding_policy"] = "composite"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(data, fh)
            tmp = fh.name
        try:
            fs2 = FeeSchedule.from_file(pathlib.Path(tmp))
            a = self.fs.expected_fee("CREDIT_CARD", Money.from_rupees_str("333.33"))
            b = fs2.expected_fee("CREDIT_CARD", Money.from_rupees_str("333.33"))
            self.assertEqual(a.gst_rounding_policy, "per_line")
            self.assertEqual(b.gst_rounding_policy, "composite")
            self.assertEqual(a.fee, b.fee)  # fee identical; only tax rounding can differ
        finally:
            os.unlink(tmp)

    def test_determinism_same_inputs_same_output(self):
        vals = {
            self.fs.expected_fee("CREDIT_CARD", Money.from_rupees_str("1234.56")).total_deduction.paise
            for _ in range(50)
        }
        self.assertEqual(len(vals), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
