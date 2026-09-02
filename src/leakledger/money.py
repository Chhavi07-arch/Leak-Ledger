"""Integer-paise money.

No float ever enters or leaves this module. Every amount in Leak Ledger is an
integer number of paise; rupees exist only for display and for parsing source
files. This is not stylistic: a float in money code produces results that differ
across runs and platforms, which would break the determinism guarantee the whole
engine rests on.

Rounding policy: HALF-UP, AWAY FROM ZERO, applied once at the point a fractional
result is produced (see `apply_bps`). Python's built-in round() is banker's
rounding (round-half-to-even) and is deliberately never used here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, List, Sequence

PAISE_PER_RUPEE = 100
BPS_DENOMINATOR = 10_000  # 1 basis point = 0.01%


class MoneyError(ValueError):
    """Raised for any construction or operation that would lose exactness."""


def _round_half_up(numerator: int, denominator: int) -> int:
    """Exact integer division, rounding halves away from zero.

    Pure integer arithmetic — no Decimal, no float, no platform dependence.
    """
    if denominator <= 0:
        raise MoneyError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    q, r = divmod(abs(numerator), denominator)
    if r * 2 >= denominator:
        q += 1
    return sign * q


@dataclass(frozen=True, order=True)
class Money:
    """An exact amount in paise. Immutable, hashable, orderable."""

    paise: int

    def __post_init__(self) -> None:
        # bool is a subclass of int; reject it explicitly so Money(True) fails.
        if isinstance(self.paise, bool) or not isinstance(self.paise, int):
            raise MoneyError(
                f"Money takes an int number of paise, got {type(self.paise).__name__}. "
                "Floats are rejected by design — parse with Money.from_rupees_str()."
            )

    # ---- construction -------------------------------------------------

    @classmethod
    def zero(cls) -> "Money":
        return cls(0)

    @classmethod
    def from_rupees_str(cls, text: str) -> "Money":
        """Parse '40.50', '-40.50', '1,234.56', '₹40.50', '40' — exactly.

        Rejects more than two decimal places rather than silently rounding:
        source data at sub-paise precision is a data problem, not a rounding
        problem, and should be quarantined by the caller.
        """
        if not isinstance(text, str):
            raise MoneyError(f"expected str, got {type(text).__name__}")
        cleaned = text.strip().replace("₹", "").replace(",", "").replace(" ", "")
        if cleaned in ("", "-", "+"):
            raise MoneyError(f"not a monetary amount: {text!r}")
        try:
            dec = Decimal(cleaned)
        except InvalidOperation:
            raise MoneyError(f"not a monetary amount: {text!r}") from None
        if dec != dec.quantize(Decimal("0.01")):
            raise MoneyError(
                f"{text!r} has sub-paise precision; quarantine it rather than rounding"
            )
        return cls(int(dec.scaleb(2)))

    @classmethod
    def from_rupees_int(cls, rupees: int) -> "Money":
        if isinstance(rupees, bool) or not isinstance(rupees, int):
            raise MoneyError("from_rupees_int takes an int")
        return cls(rupees * PAISE_PER_RUPEE)

    # ---- arithmetic ---------------------------------------------------

    def __add__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.paise + other.paise)

    def __sub__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.paise - other.paise)

    def __neg__(self) -> "Money":
        return Money(-self.paise)

    def __abs__(self) -> "Money":
        return Money(abs(self.paise))

    def __mul__(self, count: int) -> "Money":
        if isinstance(count, bool) or not isinstance(count, int):
            raise MoneyError("Money can only be multiplied by an int count")
        return Money(self.paise * count)

    __rmul__ = __mul__

    def apply_bps(self, bps: int) -> "Money":
        """Percentage in basis points, rounded half-up away from zero, once.

        40 bps of Rs 4500.00 -> Rs 18.00 exactly.
        90 bps of Rs 4500.00 -> Rs 40.50 exactly.
        """
        if isinstance(bps, bool) or not isinstance(bps, int):
            raise MoneyError("bps must be an int (1 bp = 0.01%)")
        return Money(_round_half_up(self.paise * bps, BPS_DENOMINATOR))

    def allocate(self, weights: Sequence[int]) -> List["Money"]:
        """Split across weights losing no paise (largest-remainder method).

        Needed when one settlement's fee must be attributed back to the N
        payments it covers. sum(result) == self, always.
        """
        if not weights or any(w < 0 for w in weights) or sum(weights) <= 0:
            raise MoneyError("allocate needs non-negative weights summing above zero")
        total_w = sum(weights)
        shares, remainders = [], []
        for i, w in enumerate(weights):
            num = self.paise * w
            q, r = divmod(abs(num), total_w)
            q = q if self.paise >= 0 else -q
            shares.append(q)
            remainders.append((r, i))
        leftover = self.paise - sum(shares)
        step = 1 if leftover >= 0 else -1
        for _, i in sorted(remainders, key=lambda t: (-t[0], t[1]))[: abs(leftover)]:
            shares[i] += step
        return [Money(s) for s in shares]

    @staticmethod
    def sum(items: Iterable["Money"]) -> "Money":
        total = 0
        for m in items:
            if not isinstance(m, Money):
                raise MoneyError("Money.sum takes Money values only")
            total += m.paise
        return Money(total)

    # ---- presentation -------------------------------------------------

    def to_rupees_str(self) -> str:
        sign = "-" if self.paise < 0 else ""
        whole, frac = divmod(abs(self.paise), PAISE_PER_RUPEE)
        return f"{sign}{whole}.{frac:02d}"

    def __str__(self) -> str:
        return f"Rs {self.to_rupees_str()}"

    def __repr__(self) -> str:
        return f"Money({self.paise})"
