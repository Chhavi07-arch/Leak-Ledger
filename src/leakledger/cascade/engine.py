"""The cascade: T0 canonicalise, T1 exact reference, T2 unique amount+window,
T3 settlement deviation search, T5 typed exception.

Tiers are strictly ordered and a record matched at tier n never falls through.
Tier labels are ORDINAL RANKS ASSIGNED BY RULE, not calibrated probabilities —
see PLAN.md, "Why an auto-applied match can go unreviewed". Nothing in this build
establishes that a T2 match is correct 90% of the time, and the label must never
be presented as though it did.

What the engine is allowed to know is deliberately constrained. It infers a
settlement cycle from the bank value date by business-day arithmetic, and pools
payments by capture DATE. It is never told the acquirer's cutoff TIME, so late
-evening payments that rolled into a neighbouring payout must be discovered by
search rather than looked up. Handing it `cycle_date_for_capture()` — the same
function the generator used — would make the pool correct by construction and
the search a no-op that reported a perfect score while proving nothing.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence

from ..clock import IST, BusinessCalendar, SETTLEMENT_LAG_BUSINESS_DAYS
from ..money import Money
from .subsetsum import DEFAULT_DEVIATION_BOUND, search_deviation

# --- dispositions -------------------------------------------------------
AUTO_APPLY = "AUTO_APPLY"
REVIEW = "REVIEW"
EXCEPTION = "EXCEPTION"

# --- typed exception reason codes (no untyped exits) --------------------
AMBIGUOUS_SUBSET = "AMBIGUOUS_SUBSET"
AMBIGUOUS_CANDIDATE = "AMBIGUOUS_CANDIDATE"
SEARCH_BUDGET_EXCEEDED = "SEARCH_BUDGET_EXCEEDED"
NO_RECONCILING_SET = "NO_RECONCILING_SET"
REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"
UNEXPLAINED_RESIDUAL = "UNEXPLAINED_RESIDUAL"


@dataclass
class Match:
    bank_txn_id: str
    tier: str
    disposition: str
    matched_ids: List[str] = field(default_factory=list)
    reason_code: Optional[str] = None
    evidence: str = ""
    combinations_examined: int = 0


@dataclass
class CascadeResult:
    matches: List[Match] = field(default_factory=list)

    def by_disposition(self, d: str) -> List[Match]:
        return [m for m in self.matches if m.disposition == d]

    def by_tier(self) -> Dict[str, int]:
        out: Dict[str, int] = defaultdict(int)
        for m in self.matches:
            out[m.tier] += 1
        return dict(out)

    def by_reason(self) -> Dict[str, int]:
        out: Dict[str, int] = defaultdict(int)
        for m in self.matches:
            if m.reason_code:
                out[m.reason_code] += 1
        return dict(out)


class Cascade:
    def __init__(self, *, payments, refunds, bank, calendar: BusinessCalendar,
                 adjustments=None,
                 bound: int = DEFAULT_DEVIATION_BOUND, tolerance: Money = Money(0)):
        self.payments = {p.payment_id: p for p in payments}
        self.refunds = refunds
        self.adjustments = adjustments or []
        self.bank = bank
        self.calendar = calendar
        self.bound = bound
        self.tolerance = tolerance
        self._by_capture_date: Dict[date, List[str]] = defaultdict(list)
        for p in payments:
            self._by_capture_date[p.captured_at.date()].append(p.payment_id)

    def _netted_refunds_for_cycle(self, cycle: date) -> Money:
        """Refunds that net against this payout rather than debiting separately.

        The engine HAS this data — it is in the gateway refunds export — and must
        use it. Omitting it was INC-003: the arithmetic could never close on any
        cycle containing a netted refund, and 11 of 24 settlements failed for a
        reason that had nothing to do with the search.
        """
        total = Money.zero()
        for r in self.refunds:
            if r.get("mode") != "NETTED":
                continue
            issued = datetime.fromisoformat(r["issued_at"]).date()
            if issued == cycle:
                total = total + Money.from_rupees_str(r["amount"])
        return total

    def _adjustments_for_cycle(self, cycle: date) -> Money:
        """Chargeback debits/credits and reserve holds posted in this cycle."""
        total = Money.zero()
        for a in self.adjustments:
            if date.fromisoformat(a["posted_date"]) != cycle:
                continue
            amt = Money.from_rupees_str(a["amount"])
            if a["kind"] in ("CHARGEBACK_DEBIT", "RESERVE_HELD"):
                total = total + amt
            elif a["kind"] == "CHARGEBACK_CREDIT":
                total = total - amt
        return total

    # ---- deduction arithmetic (observed, not contractual) -------------
    def _deduction_for(self, ids: Sequence[str]) -> Money:
        """Fee + GST as the gateway REPORTED them.

        Observed values, deliberately — matching must reconcile against what was
        actually charged. Comparing against the contract is a separate question,
        answered in Phase 04, and conflating the two would turn every overcharge
        into an unmatched settlement instead of a finding.
        """
        total = Money.zero()
        for i in ids:
            p = self.payments.get(i)
            if p is None:
                continue
            if p.fee_charged:
                total = total + p.fee_charged
            if p.gst_charged:
                total = total + p.gst_charged
        return total

    def _infer_cycle_date(self, value_date: date) -> date:
        """Walk back SETTLEMENT_LAG business days. Date arithmetic only — the
        engine legitimately knows T+2, and legitimately does not know the cutoff."""
        d, n = value_date, SETTLEMENT_LAG_BUSINESS_DAYS
        while n > 0:
            d -= timedelta(days=1)
            if self.calendar.is_business_day(d):
                n -= 1
        return d

    def _neighbours(self, cycle: date) -> Dict[str, Money]:
        """Late-evening payments on adjacent days: the only plausible strays.

        Restricting to >= 22:00 keeps the search space bounded without telling
        the engine where the cutoff actually is.
        """
        out: Dict[str, Money] = {}
        for delta in (-1, 1):
            for pid in self._by_capture_date.get(cycle + timedelta(days=delta), []):
                p = self.payments[pid]
                if p.captured_at.hour >= 22:
                    out[pid] = p.amount
        return out

    # ---- tiers --------------------------------------------------------
    def run(self) -> CascadeResult:
        result = CascadeResult()
        refunds_by_arn = {r.get("arn"): r for r in self.refunds if r.get("arn")}
        refunds_by_amount = defaultdict(list)
        for r in self.refunds:
            refunds_by_amount[r["amount"]].append(r)

        for b in self.bank:
            if b["direction"] == "DR":
                result.matches.append(self._match_debit(b, refunds_by_arn, refunds_by_amount))
            else:
                result.matches.append(self._match_credit(b))
        return result

    def _match_debit(self, b, refunds_by_arn, refunds_by_amount) -> Match:
        # --- T1: exact reference ---
        utr = (b.get("utr") or "").strip()
        if utr and utr in refunds_by_arn:
            r = refunds_by_arn[utr]
            if r["amount"] == b["amount"]:
                return Match(b["txn_id"], "T1", AUTO_APPLY, [r["refund_id"]],
                             evidence=f"ARN {utr} matches refund {r['refund_id']}, amounts agree")
            # exact key present but arithmetic disagrees — never trust the key alone
            return Match(b["txn_id"], "T1", EXCEPTION, [], UNEXPLAINED_RESIDUAL,
                         f"ARN {utr} matches refund {r['refund_id']} but amount "
                         f"{b['amount']} != {r['amount']}")
        # --- T2: unique amount within window ---
        cands = refunds_by_amount.get(b["amount"], [])
        bd = datetime.strptime(b["value_date"], "%d-%m-%Y").date()
        in_window = [r for r in cands
                     if abs((datetime.fromisoformat(r["issued_at"]).date() - bd).days) <= 2]
        if len(in_window) == 1:
            return Match(b["txn_id"], "T2", AUTO_APPLY, [in_window[0]["refund_id"]],
                         evidence=f"unique refund of {b['amount']} within +/-2d of {bd}")
        if len(in_window) > 1:
            return Match(b["txn_id"], "T2", EXCEPTION,
                         [r["refund_id"] for r in in_window], AMBIGUOUS_CANDIDATE,
                         f"{len(in_window)} refunds of {b['amount']} in window; refusing to choose")
        return Match(b["txn_id"], "T5", EXCEPTION, [], REFERENCE_NOT_FOUND,
                     f"debit of {b['amount']} on {bd} matches no known refund")

    def _match_credit(self, b) -> Match:
        bd = datetime.strptime(b["value_date"], "%d-%m-%Y").date()
        cycle = self._infer_cycle_date(bd)
        pool_ids = self._by_capture_date.get(cycle, [])
        if not pool_ids:
            return Match(b["txn_id"], "T5", EXCEPTION, [], NO_RECONCILING_SET,
                         f"no payments captured on inferred cycle {cycle}")
        pool = {i: self.payments[i].amount for i in pool_ids}
        neighbours = self._neighbours(cycle)
        target = Money.from_rupees_str(b["amount"])

        # netted refunds reduce this payout and are constant across deviations
        netted = self._netted_refunds_for_cycle(cycle) + self._adjustments_for_cycle(cycle)

        def deduct(ids):
            return self._deduction_for(ids) + netted

        res = search_deviation(
            target_net=target, pool=pool, neighbours=neighbours,
            deduction_for=deduct, tolerance=self.tolerance, bound=self.bound,
        )
        if res.status == "SOLVED":
            dev = res.solutions[0]
            ids = [i for i in pool_ids if i not in dev.excluded] + sorted(dev.included)
            ev = f"cycle {cycle}, pool {len(pool_ids)}, {dev.describe()}"
            if netted.paise:
                ev += f"; netted refunds {netted}"
            return Match(b["txn_id"], "T3", REVIEW, ids, evidence=ev,
                         combinations_examined=res.combinations_examined)
        if res.status == "AMBIGUOUS":
            return Match(b["txn_id"], "T3", EXCEPTION, [], AMBIGUOUS_SUBSET,
                         f"cycle {cycle}: {res.detail}", res.combinations_examined)
        if res.status == "BUDGET_EXCEEDED":
            return Match(b["txn_id"], "T3", EXCEPTION, [], SEARCH_BUDGET_EXCEEDED,
                         f"cycle {cycle}: {res.detail}", res.combinations_examined)
        return Match(b["txn_id"], "T3", EXCEPTION, [], NO_RECONCILING_SET,
                     f"cycle {cycle}, pool {len(pool_ids)}: {res.detail}",
                     res.combinations_examined)
