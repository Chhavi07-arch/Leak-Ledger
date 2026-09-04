"""Versioned fee schedule: expected fee, GST, and the arithmetic behind them.

Two properties matter more than the rates themselves.

1.  Every computation returns its own derivation. A leakage finding a human
    cannot re-derive in ten seconds is a finding they will not act on.
2.  The file is hashed by content and the hash travels in the run manifest, so
    any result can be reproduced against the exact contract it was computed
    under. Editing the schedule changes the hash and therefore the provenance
    of every number downstream.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .money import Money

GST_ROUNDING_POLICIES = ("per_line", "composite")


class FeeScheduleError(ValueError):
    """Raised when an instrument or bank is absent from the schedule.

    Never guessed. An unknown instrument becomes a typed exception
    (FEE_SLAB_UNKNOWN), because inventing a rate would manufacture a leakage
    finding that has no contractual basis.
    """


@dataclass(frozen=True)
class FeeComputation:
    """A fee, its tax, and the full derivation that produced them."""

    instrument: str
    amount: Money
    slab_label: str
    rate_bps: Optional[int]
    flat_paise: Optional[int]
    fee: Money
    gst: Money
    total_deduction: Money
    schedule_version: str
    schedule_sha256: str
    gst_rounding_policy: str

    def derivation(self) -> str:
        if self.rate_bps is not None:
            basis = (
                f"{self.rate_bps} bps ({self.rate_bps / 100:.2f}%) x "
                f"Rs {self.amount.to_rupees_str()} = Rs {self.fee.to_rupees_str()}"
            )
        else:
            basis = f"flat Rs {self.fee.to_rupees_str()} per transaction"
        return (
            f"slab={self.slab_label}; {basis}; "
            f"GST 18% ({self.gst_rounding_policy}) = Rs {self.gst.to_rupees_str()}; "
            f"total deduction = Rs {self.total_deduction.to_rupees_str()}; "
            f"schedule={self.schedule_version} sha256={self.schedule_sha256[:12]}"
        )


@dataclass
class FeeSchedule:
    version: str
    gst_bps: int
    tds_bps: int
    gst_rounding_policy: str
    instruments: Dict[str, Any]
    sha256: str
    source_path: str = ""
    _raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_file(cls, path: Path) -> "FeeSchedule":
        p = Path(path)
        raw_bytes = p.read_bytes()          # hash the bytes on disk, not the parsed dict
        digest = hashlib.sha256(raw_bytes).hexdigest()
        data = json.loads(raw_bytes.decode("utf-8"))
        policy = data.get("gst_rounding_policy", "per_line")
        if policy not in GST_ROUNDING_POLICIES:
            raise FeeScheduleError(
                f"unknown gst_rounding_policy {policy!r}; expected one of {GST_ROUNDING_POLICIES}"
            )
        return cls(
            version=data["version"],
            gst_bps=data["gst_bps"],
            tds_bps=data.get("tds_bps", 0),
            gst_rounding_policy=policy,
            instruments=data["instruments"],
            sha256=digest,
            source_path=str(p),
            _raw=data,
        )

    # ---- computation --------------------------------------------------

    def _select_slab(self, instrument: str, amount: Money, is_international: bool) -> Dict[str, Any]:
        spec = self.instruments.get(instrument)
        if spec is None:
            raise FeeScheduleError(
                f"instrument {instrument!r} absent from {self.version} — not guessed"
            )
        key = "international_slabs" if (is_international and "international_slabs" in spec) else "slabs"
        for slab in spec[key]:
            cap = slab["max_paise"]
            if cap is None or amount.paise <= cap:   # inclusive upper bound
                return slab
        raise FeeScheduleError(
            f"no slab in {self.version} covers Rs {amount.to_rupees_str()} for {instrument}"
        )

    def expected_tds(self, amount: Money) -> Money:
        """TDS withheld on the GROSS transaction value (s.194-O).

        Deliberately per-payment rather than per-cycle. The deviation search in
        T3 reduces reconciliation to a signed subset-sum only because every
        deduction is additive over individual payments; a cycle-level TDS term
        would break that reduction and force the search back to enumerating
        combinations.
        """
        return amount.apply_bps(self.tds_bps)

    def expected_fee(
        self,
        instrument: str,
        amount: Money,
        *,
        is_international: bool = False,
        bank: Optional[str] = None,
    ) -> FeeComputation:
        spec = self.instruments.get(instrument)
        if spec is None:
            raise FeeScheduleError(
                f"instrument {instrument!r} absent from {self.version} — not guessed"
            )

        if spec["kind"] == "flat_per_bank":
            if bank is None:
                raise FeeScheduleError(
                    f"{instrument} is priced per bank; bank is required, not defaulted silently"
                )
            flat = spec["banks"].get(bank)
            if flat is None:
                flat = spec["default_paise"]
                label = f"{instrument}_DEFAULT"
            else:
                label = f"{instrument}_{bank}"
            fee, rate_bps, flat_paise = Money(flat), None, flat
        else:
            slab = self._select_slab(instrument, amount, is_international)
            label = slab["label"]
            rate_bps, flat_paise = slab["bps"], None
            fee = amount.apply_bps(rate_bps)

        if self.gst_rounding_policy == "per_line":
            # Fee is already an exact paise value; GST is 18% of that, rounded once.
            gst = fee.apply_bps(self.gst_bps)
        else:  # composite: single rounding over the combined fraction
            if rate_bps is not None:
                combined = amount.paise * rate_bps * (10_000 + self.gst_bps)
                total = Money(_round_half_up_2(combined, 10_000 * 10_000))
            else:
                total = Money(flat_paise).apply_bps(10_000 + self.gst_bps)
            gst = total - fee

        return FeeComputation(
            instrument=instrument,
            amount=amount,
            slab_label=label,
            rate_bps=rate_bps,
            flat_paise=flat_paise,
            fee=fee,
            gst=gst,
            total_deduction=fee + gst,
            schedule_version=self.version,
            schedule_sha256=self.sha256,
            gst_rounding_policy=self.gst_rounding_policy,
        )


def _round_half_up_2(numerator: int, denominator: int) -> int:
    sign = -1 if numerator < 0 else 1
    q, r = divmod(abs(numerator), denominator)
    if r * 2 >= denominator:
        q += 1
    return sign * q
