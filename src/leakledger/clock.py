"""Time, timezone and the settlement cycle.

Every datetime in Leak Ledger is timezone-aware and expressed in IST. Naive
datetimes are rejected at the boundary rather than assumed to be local: a naive
timestamp silently interpreted as the wrong zone moves a payment into the wrong
settlement cycle, which is indistinguishable from a missing settlement.

India observes no DST, but Asia/Kolkata is used rather than a hardcoded +05:30
offset so the source of truth is the tz database, not a constant in this file.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Set
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# --- The named constant -------------------------------------------------
# A payment captured AT OR AFTER this wall-clock time belongs to the NEXT
# settlement cycle. Boundary is inclusive-of-next: 23:00:00 rolls over.
# This value is why a capture at 23:58 IST is a straddle case and not a
# same-day settlement.
SETTLEMENT_CUTOFF_IST = time(23, 0, 0)

# Standard settlement lag in business days (T+2).
SETTLEMENT_LAG_BUSINESS_DAYS = 2


class ClockError(ValueError):
    """Raised when a timestamp is naive, unparseable, or out of contract."""


def require_aware(dt: datetime) -> datetime:
    """Reject naive datetimes. Returns the value in IST."""
    if not isinstance(dt, datetime):
        raise ClockError(f"expected datetime, got {type(dt).__name__}")
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ClockError(
            "naive datetime rejected — every timestamp must carry a timezone. "
            "Parse with clock.parse_ist() or attach tzinfo at ingest."
        )
    return dt.astimezone(IST)


def parse_ist(text: str) -> datetime:
    """Parse an ISO-8601 timestamp into an IST-aware datetime.

    A string without an offset is treated as IST *explicitly* — that is a
    documented ingest convention, not an accident of the host's locale.
    """
    if not isinstance(text, str):
        raise ClockError(f"expected str, got {type(text).__name__}")
    raw = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise ClockError(f"not an ISO-8601 timestamp: {text!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def parse_ist_date(text: str) -> date:
    if not isinstance(text, str):
        raise ClockError(f"expected str, got {type(text).__name__}")
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise ClockError(f"not an ISO-8601 date: {text!r}") from None


class BusinessCalendar:
    """Weekend and bank-holiday awareness for settlement dating.

    T+2 across a Saturday is not two calendar days. Holidays are configuration,
    not code, so the calendar can be corrected without touching the engine.
    """

    def __init__(self, holidays: Iterable[date]):
        self.holidays: Set[date] = set(holidays)

    @classmethod
    def from_file(cls, path: Path) -> "BusinessCalendar":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(date.fromisoformat(d) for d in data["holidays"])

    def is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def add_business_days(self, start: date, n: int) -> date:
        if n < 0:
            raise ClockError("add_business_days expects n >= 0")
        d, remaining = start, n
        while remaining > 0:
            d += timedelta(days=1)
            if self.is_business_day(d):
                remaining -= 1
        return d


def cycle_date_for_capture(captured_at: datetime) -> date:
    """The settlement cycle a capture belongs to, applying the cutoff.

    At or after SETTLEMENT_CUTOFF_IST the capture rolls into the next cycle.
    """
    ist = require_aware(captured_at)
    return ist.date() + timedelta(days=1) if ist.time() >= SETTLEMENT_CUTOFF_IST else ist.date()


def expected_settlement_date(captured_at: datetime, calendar: BusinessCalendar) -> date:
    """When the money for this capture should reach the bank (T+2 business)."""
    return calendar.add_business_days(
        cycle_date_for_capture(captured_at), SETTLEMENT_LAG_BUSINESS_DAYS
    )
