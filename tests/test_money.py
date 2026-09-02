"""Money is the foundation every later number rests on, so it is tested against
hand-computed values that come from no generator in this repo."""
import unittest, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from leakledger.money import Money, MoneyError, _round_half_up


class TestFloatRejection(unittest.TestCase):
    """The single most important guard in the codebase."""

    def test_float_rejected_at_construction(self):
        with self.assertRaises(MoneyError):
            Money(4.5)

    def test_float_zero_rejected(self):
        with self.assertRaises(MoneyError):
            Money(0.0)

    def test_bool_rejected(self):
        with self.assertRaises(MoneyError):
            Money(True)

    def test_float_rejected_in_multiplication(self):
        with self.assertRaises(MoneyError):
            Money(100) * 1.5

    def test_float_rejected_in_bps(self):
        with self.assertRaises(MoneyError):
            Money(100).apply_bps(0.5)


class TestHalfUpRounding(unittest.TestCase):
    """Python's round() is banker's rounding. These assert we are NOT using it."""

    def test_half_rounds_up_not_to_even(self):
        self.assertEqual(_round_half_up(1, 2), 1)    # 0.5 -> 1  (banker's gives 0)
        self.assertEqual(_round_half_up(3, 2), 2)    # 1.5 -> 2
        self.assertEqual(_round_half_up(5, 2), 3)    # 2.5 -> 3  (banker's gives 2)
        self.assertEqual(_round_half_up(7, 2), 4)    # 3.5 -> 4

    def test_python_round_would_disagree(self):
        # Documents precisely why the builtin is unusable here.
        self.assertNotEqual(_round_half_up(5, 2), round(2.5))

    def test_negative_halves_round_away_from_zero(self):
        self.assertEqual(_round_half_up(-1, 2), -1)
        self.assertEqual(_round_half_up(-5, 2), -3)

    def test_below_half_rounds_down(self):
        self.assertEqual(_round_half_up(4, 10), 0)
        self.assertEqual(_round_half_up(49, 100), 0)


class TestParsing(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(Money.from_rupees_str("40.50").paise, 4050)

    def test_symbol_and_separators(self):
        self.assertEqual(Money.from_rupees_str("Rs 1,234.56".replace("Rs ", "")).paise, 123456)
        self.assertEqual(Money.from_rupees_str("₹1,234.56").paise, 123456)

    def test_negative(self):
        self.assertEqual(Money.from_rupees_str("-40.50").paise, -4050)

    def test_integer_rupees(self):
        self.assertEqual(Money.from_rupees_str("40").paise, 4000)

    def test_sub_paise_rejected_not_rounded(self):
        """A third decimal is a data problem; quarantine beats silent rounding."""
        with self.assertRaises(MoneyError):
            Money.from_rupees_str("12.345")

    def test_garbage_rejected(self):
        for bad in ("", "abc", "-", "1.2.3"):
            with self.assertRaises(MoneyError):
                Money.from_rupees_str(bad)

    def test_roundtrip(self):
        for s in ("0.00", "40.50", "-40.50", "1234.56"):
            self.assertEqual(Money.from_rupees_str(s).to_rupees_str(), s)


class TestBps(unittest.TestCase):
    """Hand-computed from the fee schedule's stated slabs."""

    def test_90bps_of_4500(self):
        # 0.90% of Rs 4500.00 = Rs 40.50 — the worked example in PLAN.
        self.assertEqual(Money.from_rupees_str("4500.00").apply_bps(90).to_rupees_str(), "40.50")

    def test_40bps_of_4500(self):
        self.assertEqual(Money.from_rupees_str("4500.00").apply_bps(40).to_rupees_str(), "18.00")

    def test_200bps_of_1999_99(self):
        # 2.00% of 1999.99 = 39.9998 -> 40.00 half-up
        self.assertEqual(Money.from_rupees_str("1999.99").apply_bps(200).to_rupees_str(), "40.00")

    def test_gst_on_40_50(self):
        # 18% of Rs 40.50 = Rs 7.29 exactly
        self.assertEqual(Money.from_rupees_str("40.50").apply_bps(1800).to_rupees_str(), "7.29")

    def test_zero_bps_is_zero(self):
        self.assertEqual(Money.from_rupees_str("999.00").apply_bps(0).paise, 0)

    def test_rounding_boundary_exact_half(self):
        # 50 bps of Rs 1.01 = 0.505 paise*100 -> exactly .5 case, must round up
        self.assertEqual(Money(101).apply_bps(50).paise, 1)


class TestAllocate(unittest.TestCase):
    """Fee attribution across an N:1 settlement must not lose or invent paise."""

    def test_no_paise_lost_uneven(self):
        parts = Money(100).allocate([1, 1, 1])
        self.assertEqual(Money.sum(parts).paise, 100)
        self.assertEqual(sorted(p.paise for p in parts), [33, 33, 34])

    def test_no_paise_lost_weighted(self):
        total = Money(10_000)
        parts = total.allocate([4500, 3300, 2200])
        self.assertEqual(Money.sum(parts), total)

    def test_negative_total_allocates_exactly(self):
        total = Money(-100)
        parts = total.allocate([1, 1, 1])
        self.assertEqual(Money.sum(parts), total)

    def test_rejects_zero_weights(self):
        with self.assertRaises(MoneyError):
            Money(100).allocate([0, 0])

    def test_deterministic_across_calls(self):
        a = [p.paise for p in Money(100).allocate([1, 1, 1])]
        b = [p.paise for p in Money(100).allocate([1, 1, 1])]
        self.assertEqual(a, b)


class TestArithmetic(unittest.TestCase):
    def test_sum_of_many_small_is_exact(self):
        """The drift a float implementation would show, absent by construction."""
        items = [Money(1)] * 1_000_000
        self.assertEqual(Money.sum(items).paise, 1_000_000)

    def test_add_sub_inverse(self):
        a, b = Money(4050), Money(729)
        self.assertEqual((a + b) - b, a)

    def test_ordering_and_equality(self):
        self.assertLess(Money(1), Money(2))
        self.assertEqual(Money(4050), Money.from_rupees_str("40.50"))

    def test_hashable(self):
        self.assertEqual(len({Money(1), Money(1), Money(2)}), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
