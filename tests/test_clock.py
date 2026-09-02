import unittest, sys, pathlib
from datetime import date, datetime, time, timezone
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from leakledger.clock import (
    IST, SETTLEMENT_CUTOFF_IST, SETTLEMENT_LAG_BUSINESS_DAYS, BusinessCalendar,
    ClockError, cycle_date_for_capture, expected_settlement_date, parse_ist, parse_ist_date,
    require_aware,
)

CONFIG = pathlib.Path(__file__).resolve().parents[1] / "config" / "holidays_2026.json"


class TestNaiveRejection(unittest.TestCase):
    def test_naive_datetime_rejected(self):
        with self.assertRaises(ClockError):
            require_aware(datetime(2026, 6, 10, 23, 58))

    def test_naive_rejected_by_cycle_date(self):
        with self.assertRaises(ClockError):
            cycle_date_for_capture(datetime(2026, 6, 10, 23, 58))

    def test_aware_accepted(self):
        self.assertIsNotNone(require_aware(datetime(2026, 6, 10, 23, 58, tzinfo=IST)))


class TestCutoffConstant(unittest.TestCase):
    def test_constant_is_named_and_23_00(self):
        self.assertEqual(SETTLEMENT_CUTOFF_IST, time(23, 0, 0))

    def test_before_cutoff_stays_same_day(self):
        self.assertEqual(cycle_date_for_capture(parse_ist("2026-06-10T22:59:59")), date(2026, 6, 10))

    def test_exactly_at_cutoff_rolls_over(self):
        """Boundary is inclusive-of-next; documented, and asserted here."""
        self.assertEqual(cycle_date_for_capture(parse_ist("2026-06-10T23:00:00")), date(2026, 6, 11))

    def test_the_2358_straddle_case(self):
        """PLAN's worked example: a 23:58 capture is a straddle, not same-day."""
        self.assertEqual(cycle_date_for_capture(parse_ist("2026-06-10T23:58:00")), date(2026, 6, 11))

    def test_midnight_is_same_day_next_cycle(self):
        self.assertEqual(cycle_date_for_capture(parse_ist("2026-06-11T00:01:00")), date(2026, 6, 11))


class TestTimezoneConversion(unittest.TestCase):
    def test_utc_input_converted_to_ist_before_cutoff_test(self):
        # 18:30 UTC == 00:00 IST next day -> next cycle date
        dt = datetime(2026, 6, 10, 18, 30, tzinfo=timezone.utc)
        self.assertEqual(cycle_date_for_capture(dt), date(2026, 6, 11))

    def test_utc_just_before_rollover(self):
        # 17:29 UTC == 22:59 IST same day
        dt = datetime(2026, 6, 10, 17, 29, tzinfo=timezone.utc)
        self.assertEqual(cycle_date_for_capture(dt), date(2026, 6, 10))

    def test_z_suffix_parsed(self):
        self.assertEqual(parse_ist("2026-06-10T18:30:00Z").hour, 0)

    def test_offsetless_string_treated_as_ist(self):
        self.assertEqual(parse_ist("2026-06-10T10:00:00").utcoffset().total_seconds(), 19800)

    def test_bad_timestamp_rejected(self):
        for bad in ("", "not-a-date", "2026-13-45T99:99:99"):
            with self.assertRaises(ClockError):
                parse_ist(bad)


class TestBusinessCalendar(unittest.TestCase):
    def setUp(self):
        self.cal = BusinessCalendar.from_file(CONFIG)

    def test_weekend_is_not_business_day(self):
        self.assertFalse(self.cal.is_business_day(date(2026, 6, 13)))  # Saturday
        self.assertFalse(self.cal.is_business_day(date(2026, 6, 14)))  # Sunday

    def test_configured_holiday_is_not_business_day(self):
        self.assertFalse(self.cal.is_business_day(date(2026, 8, 15)))

    def test_t2_across_a_weekend_is_not_two_calendar_days(self):
        """The case the whole calendar exists for."""
        settled = expected_settlement_date(parse_ist("2026-06-12T10:00:00"), self.cal)  # Friday
        self.assertEqual(settled, date(2026, 6, 16))  # Tue, not Sun the 14th
        self.assertNotEqual(settled, date(2026, 6, 14))

    def test_t2_on_a_plain_midweek_day(self):
        self.assertEqual(
            expected_settlement_date(parse_ist("2026-06-09T10:00:00"), self.cal), date(2026, 6, 11)
        )

    def test_cutoff_and_calendar_compose(self):
        """23:58 Friday rolls to Saturday's cycle, then T+2 business days."""
        self.assertEqual(
            expected_settlement_date(parse_ist("2026-06-12T23:58:00"), self.cal), date(2026, 6, 16)
        )

    def test_holiday_extends_settlement(self):
        # 2026-08-15 is a configured holiday and falls on a Saturday; 13th (Thu) + 2 -> 17th? verify
        got = expected_settlement_date(parse_ist("2026-08-13T10:00:00"), self.cal)
        self.assertTrue(self.cal.is_business_day(got))

    def test_lag_constant_is_named(self):
        self.assertEqual(SETTLEMENT_LAG_BUSINESS_DAYS, 2)

    def test_negative_days_rejected(self):
        with self.assertRaises(ClockError):
            self.cal.add_business_days(date(2026, 6, 10), -1)


class TestDateParsing(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_ist_date("2026-06-10"), date(2026, 6, 10))

    def test_invalid_rejected(self):
        with self.assertRaises(ClockError):
            parse_ist_date("10/06/2026")


if __name__ == "__main__":
    unittest.main(verbosity=2)
