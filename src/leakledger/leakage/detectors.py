"""Ten typed detectors over reconciled state.

Each is a deterministic rule whose verdict a human can re-derive from the
derivation string alone. No model, no score, no threshold that cannot be
explained to an auditor in one sentence.

CIRCULARITY, STATED WHERE IT LIVES. The four contract-dependent rules
(FEE_OVERCHARGE, GST_MISMATCH, SHORT_SETTLEMENT, ZERO_MDR_VIOLATION) compare
against a fee schedule authored in this repository, from which the batch was also
generated. They verify that the arithmetic and the plumbing are correct; they do
not discover anything nobody put there. The six structural rules do not consult
the schedule at all and are the half of the taxonomy that carries the argument.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from ..clock import BusinessCalendar, SETTLEMENT_LAG_BUSINESS_DAYS
from ..feeschedule import FeeSchedule, FeeScheduleError
from ..money import Money
from .findings import FindingSet

# grace beyond T+2 before a captured payment is considered unsettled
MISSING_SETTLEMENT_GRACE_DAYS = 3
# a won dispute should be re-credited within this many days
CHARGEBACK_RECREDIT_SLA_DAYS = 21
# a held reserve should be released within this many days
RESERVE_RELEASE_SLA_DAYS = 10
# two captures against one order within this window are a duplicate, not a retry
DUPLICATE_CAPTURE_WINDOW_MINUTES = 60
# a genuine double credit can straddle a posting-lag day
DUPLICATE_PAYOUT_WINDOW_DAYS = 2
# an ARN-less refund may only be vouched for by a debit near its issue date
REFUND_MATCH_WINDOW_DAYS = 3


# =====================================================================
#  CONTRACT-DEPENDENT — verification, not discovery
# =====================================================================

def detect_fee_overcharge(fs: FeeSchedule, payments, out: FindingSet) -> None:
    """Charged fee exceeds the contracted slab for this instrument and amount."""
    for p in payments:
        if p.fee_charged is None:
            continue
        if p.instrument == "UPI":
            # a fee on a zero-MDR leg is ZERO_MDR_VIOLATION, not an overcharge.
            # Counting it as both reports one defect twice and inflates both
            # the finding count and the recovered value.
            continue
        try:
            comp = fs.expected_fee(p.instrument, p.amount,
                                   is_international=p.is_international, bank=p.bank)
        except FeeScheduleError:
            continue                      # unknown slab is an exception, not a finding
        delta = p.fee_charged - comp.fee
        if delta.paise > 0:
            out.add("FEE_OVERCHARGE", p.payment_id, delta,
                    f"expected {comp.rate_bps if comp.rate_bps is not None else 'flat'}"
                    f"{' bps' if comp.rate_bps is not None else ''} on Rs "
                    f"{p.amount.to_rupees_str()} = Rs {comp.fee.to_rupees_str()}; "
                    f"charged Rs {p.fee_charged.to_rupees_str()}; "
                    f"delta Rs {delta.to_rupees_str()}; slab={comp.slab_label}; "
                    f"schedule={comp.schedule_version} sha={comp.schedule_sha256[:12]}",
                    expected_paise=comp.fee.paise, charged_paise=p.fee_charged.paise,
                    slab=comp.slab_label)


def detect_gst_mismatch(fs: FeeSchedule, payments, out: FindingSet) -> None:
    """Tax line inconsistent with 18% of the fee actually stated.

    Computed against the CHARGED fee, not the contracted one: the question is
    whether the tax line is internally consistent with the invoice it sits on. A
    payment can be overcharged AND correctly taxed, and conflating the two would
    double-count one defect as two.
    """
    for p in payments:
        if p.fee_charged is None or p.gst_charged is None:
            continue
        expected = p.fee_charged.apply_bps(fs.gst_bps)
        delta = p.gst_charged - expected
        if delta.paise != 0:
            out.add("GST_MISMATCH", p.payment_id, abs(delta),
                    f"18% of stated fee Rs {p.fee_charged.to_rupees_str()} = Rs "
                    f"{expected.to_rupees_str()} ({fs.gst_rounding_policy}); "
                    f"charged Rs {p.gst_charged.to_rupees_str()}; "
                    f"delta Rs {delta.to_rupees_str()}",
                    expected_paise=expected.paise, charged_paise=p.gst_charged.paise,
                    policy=fs.gst_rounding_policy)


def detect_zero_mdr_violation(fs: FeeSchedule, payments, out: FindingSet) -> None:
    """Any non-zero fee on a zero-MDR UPI P2M leg.

    Precision is 1.0 by construction -- this is an `if fee > 0` on an instrument
    the schedule prices at zero. Reported as a RULE_CHECK and excluded from
    aggregate precision/recall for exactly that reason.
    """
    for p in payments:
        if p.instrument != "UPI" or p.fee_charged is None:
            continue
        if p.fee_charged.paise > 0:
            out.add("ZERO_MDR_VIOLATION", p.payment_id, p.fee_charged,
                    f"UPI P2M is zero-MDR under {fs.version}; fee of Rs "
                    f"{p.fee_charged.to_rupees_str()} charged on Rs "
                    f"{p.amount.to_rupees_str()}",
                    charged_paise=p.fee_charged.paise)


def detect_short_settlement(cascade_result, bank_rows, payments, refunds,
                            adjustments, fs, out: FindingSet) -> None:
    """A payout whose own T+2 cycle has payments but will not reconcile.

    DETECTABLE BUT NOT QUANTIFIABLE, and the distinction is deliberate.

    The detection signal is clean: PRIMARY_CYCLE_UNRECONCILED means the credit's
    own cycle has payments and no deviation within bound reconciles it, and the
    engine refused to attribute the credit to a more distant cycle. On the
    generated batch this fires on exactly the settlements seeded short.

    The VALUE cannot be honestly reported. The nearest-residual search absorbs
    the shortfall by re-attributing payments, and how much it absorbs depends
    entirely on the deviation bound. Measured against two seeded shortfalls of
    Rs 52.14 and Rs 114.60:

        bound   d=0          d=1       d=2       d=6
        ----------------------------------------------
        stl_0006  Rs 21,850.20  Rs 621.16  Rs  52.14  Rs 0.01
        stl_0017  Rs  6,223.08  Rs 841.09  Rs 146.63  Rs 0.04

    d=2 reproduces one seeded value exactly and misses the other by 28%.
    Selecting d=2 for residual reporting while matching at d=6 would be tuning a
    parameter until a number matched ground truth -- the exact self-grading this
    build argues against, and the same error class as INC-010, where a
    plausible-looking figure was reported as established fact.

    The finding therefore carries value ZERO and says so. It contributes nothing
    to the headline total and everything to the exception queue, which is where a
    "this payout does not add up and I cannot tell you by how much" belongs.
    """
    by_txn = {b["txn_id"]: b for b in bank_rows}
    for m in cascade_result.matches:
        if m.reason_code != "PRIMARY_CYCLE_UNRECONCILED":
            continue
        b = by_txn.get(m.bank_txn_id)
        if not b:
            continue
        near = ""
        if m.residual_paise is not None:
            near = (f"; nearest reconciliation at d<={m.residual_deviation_size} misses by "
                    f"Rs {m.residual_paise / 100:,.2f}, which is NOT the shortfall -- the "
                    f"search absorbs the gap by re-attributing payments, so no value is claimed")
        out.add("SHORT_SETTLEMENT", m.bank_txn_id, Money.zero(),
                f"credit of Rs {b['amount']} on {b['value_date']}: its own T+"
                f"{SETTLEMENT_LAG_BUSINESS_DAYS} cycle has payments but does not reconcile; "
                f"value UNQUANTIFIED{near}",
                bank_amount=b["amount"], unquantified=True,
                nearest_residual_paise=m.residual_paise)


# =====================================================================
#  STRUCTURAL — no contract rate consulted
# =====================================================================

def detect_missing_settlement(payments, cascade_result, calendar: BusinessCalendar,
                              as_of: date, out: FindingSet, bank_rows=None,
                              covered_cycles=None, lag_tolerance_days: int = 2) -> None:
    """Captured money for which NO payout plausibly exists.

    AN EXCEPTION IS NOT A LEAK. The first version of this rule flagged every
    payment the cascade had not positively matched, which swept in every payout
    the cascade had merely REFUSED -- ambiguous subsets, deviation-bound
    exceedances. That flagged 270 payments where only 76 were genuinely unpaid:
    194 of them had settled perfectly well and the engine simply could not prove
    it. The headline leakage figure was inflated roughly 3x by claiming ignorance
    as loss.

    The rule now requires POSITIVE evidence of absence: a payment is only missing
    if no bank credit exists anywhere in the plausible value-date window for its
    cycle. Where a credit exists but could not be reconciled, that is an exception
    and belongs in the exception queue, not in the findings.
    """
    settled = set()
    for m in cascade_result.matches:
        if m.disposition in ("AUTO_APPLY", "REVIEW"):
            settled.update(m.matched_ids)

    # A cycle is COVERED if some bank credit could plausibly belong to it, using
    # the same non-injective T+2 inversion the cascade uses. Testing "was there a
    # credit near this date" is too coarse -- with a credit on most dates it never
    # fires. Testing "could any credit belong to THIS cycle" is the precise
    # question, and absence of any such credit is positive evidence of a missing
    # payout rather than of an unmatched one.
    # A cycle counts as covered only if a credit that is NOT already spoken for
    # could belong to it. Testing mere compatibility is too weak: with T+2 dating
    # non-injective and a straddle allowance on either side, almost every cycle is
    # compatible with some credit, and the rule never fires. Availability is the
    # honest question -- if every plausible credit has already been attributed to
    # another cycle, no payout arrived for this one.
    covered = set(covered_cycles or ())

    by_cycle: Dict[date, List] = defaultdict(list)
    for p in payments:
        if p.status != "CAPTURED" or p.payment_id in settled:
            continue
        due = calendar.add_business_days(p.captured_at.date(), SETTLEMENT_LAG_BUSINESS_DAYS)
        if (as_of - due).days <= MISSING_SETTLEMENT_GRACE_DAYS:
            continue
        cap = p.captured_at.date()
        # the engine does not know the cutoff, so either day is a plausible cycle
        if {cap, cap + timedelta(days=1)} <= covered:
            continue          # a payout did arrive; inability to match it is an exception
        if {cap, cap + timedelta(days=1)} & covered and p.captured_at.hour >= 22:
            continue          # a late capture genuinely could have rolled into a covered cycle
        by_cycle[due].append(p)
    for due, group in sorted(by_cycle.items()):
        total = Money.sum(p.amount for p in group)
        out.add("MISSING_SETTLEMENT", f"cycle_due_{due}", total,
                f"{len(group)} captured payments totalling Rs {total.to_rupees_str()} "
                f"due T+{SETTLEMENT_LAG_BUSINESS_DAYS} on {due}; NO bank credit on any "
                f"date in {due}..{due + timedelta(days=2)}, {(as_of - due).days} days "
                f"later (grace {MISSING_SETTLEMENT_GRACE_DAYS}d)",
                payment_ids=[p.payment_id for p in group], due=str(due))


def detect_duplicate_payout(bank_rows, out: FindingSet) -> None:
    """The same payout credited twice: identical UTR, amount and value date."""
    # Keyed on AMOUNT within a short value-date window, not on an exact
    # (utr, amount, date) triple. The bank applies posting lag and drops the
    # structured UTR field independently per row, so a genuine double credit can
    # arrive on different dates with only one of them carrying a reference. The
    # strict key found 1 of 2 real duplicates.
    credits = [b for b in bank_rows if b["direction"] == "CR"]
    by_amount: Dict[str, List] = defaultdict(list)
    for b in credits:
        by_amount[b["amount"]].append(b)
    for amount, rows in sorted(by_amount.items()):
        if len(rows) < 2:
            continue
        rows = sorted(rows, key=lambda r: datetime.strptime(r["value_date"], "%d-%m-%Y").date())
        group = [rows[0]]
        for prev, cur in zip(rows, rows[1:]):
            d0 = datetime.strptime(prev["value_date"], "%d-%m-%Y").date()
            d1 = datetime.strptime(cur["value_date"], "%d-%m-%Y").date()
            if (d1 - d0).days <= DUPLICATE_PAYOUT_WINDOW_DAYS:
                group.append(cur)
            else:
                group = [cur]
            if len(group) >= 2:
                utrs = {(r.get("utr") or "").strip() for r in group} - {""}
                if len(utrs) > 1:
                    continue          # different references: two distinct payouts
                extra = Money.from_rupees_str(amount) * (len(group) - 1)
                out.add("DUPLICATE_PAYOUT", group[0]["txn_id"], extra,
                        f"Rs {amount} credited {len(group)}x within "
                        f"{DUPLICATE_PAYOUT_WINDOW_DAYS}d "
                        f"({', '.join(r['txn_id'] + '@' + r['value_date'] for r in group)}); "
                        f"reference {utrs.pop() if utrs else 'absent on all rows'}; "
                        f"Rs {extra.to_rupees_str()} received in excess",
                        txn_ids=[r["txn_id"] for r in group])
                group = [cur]


def detect_duplicate_capture(payments, out: FindingSet) -> None:
    """Two captures against one order within a short window: the customer paid twice."""
    # T0 canonicalisation: a gateway export can repeat the same payment row.
    # That is a file artefact, not a second charge; without this the rule counts
    # export noise as customer harm.
    seen_ids = set()
    canonical = []
    for p in payments:
        if p.status != "CAPTURED" or p.payment_id in seen_ids:
            continue
        seen_ids.add(p.payment_id)
        canonical.append(p)
    by_order: Dict[str, List] = defaultdict(list)
    for p in canonical:
        by_order[p.order_id].append(p)
    for order_id, group in sorted(by_order.items()):
        if len(group) < 2:
            continue
        group.sort(key=lambda p: p.captured_at)
        for first, second in zip(group, group[1:]):
            gap = (second.captured_at - first.captured_at).total_seconds() / 60
            if gap <= DUPLICATE_CAPTURE_WINDOW_MINUTES and second.amount == first.amount:
                out.add("DUPLICATE_CAPTURE", second.payment_id, second.amount,
                        f"order {order_id} captured twice at Rs "
                        f"{second.amount.to_rupees_str()}: {first.payment_id} at "
                        f"{first.captured_at:%H:%M} and {second.payment_id} at "
                        f"{second.captured_at:%H:%M}, {gap:.0f} min apart "
                        f"(window {DUPLICATE_CAPTURE_WINDOW_MINUTES} min)",
                        first=first.payment_id, second=second.payment_id)


def detect_refund_not_reached(refunds, bank_rows, out: FindingSet) -> None:
    """A refund deducted from the merchant with no outbound leg to the customer."""
    debits = defaultdict(list)
    for b in bank_rows:
        if b["direction"] == "DR":
            debits[b["amount"]].append(b)
    for r in refunds:
        if r.get("mode") != "INSTANT":
            # A NETTED refund reduces the payout and has no outbound leg at all,
            # so "it never reached the customer" produces no observable difference
            # in payments + bank data. Undetectable in principle, not merely
            # unimplemented -- recorded as a scope limit, not silently skipped.
            continue
        amt = r["amount"]
        arn = (r.get("arn") or "").strip()
        # Reference matching is tried three ways before falling back, because the
        # bank drops its structured UTR field on roughly two thirds of debits.
        # Requiring the structured field alone treated 5 of 8 genuinely-paid
        # refunds as unpaid: absence of a reference on the bank statement is not
        # evidence that money did not move. The ARN survives inside the free-text
        # narration even when the field is blank, and finding it there is an exact
        # substring test -- deterministic, no model involved.
        issued = datetime.fromisoformat(r["issued_at"]).date()
        matched = []
        if arn:
            for b in debits.get(amt, []):
                if (b.get("utr") or "").strip() == arn or arn in (b.get("narration") or ""):
                    matched.append(b)
        if not matched:
            # no usable reference on either side: an amount match inside the
            # refund's own date window is the most that can honestly be claimed
            for b in debits.get(amt, []):
                bd = datetime.strptime(b["value_date"], "%d-%m-%Y").date()
                if abs((bd - issued).days) <= REFUND_MATCH_WINDOW_DAYS:
                    matched.append(b)
        if not matched:
            out.add("REFUND_NOT_REACHED", r["refund_id"], Money.from_rupees_str(amt),
                    f"instant refund {r['refund_id']} of Rs {amt} issued "
                    f"{r['issued_at'][:10]} (ARN {arn or 'absent'}) has no matching "
                    f"outbound debit on the bank statement",
                    payment_id=r.get("payment_id"), arn=arn)


def detect_chargeback_not_recredited(adjustments, as_of: date, out: FindingSet) -> None:
    """A dispute won but never re-credited within SLA."""
    credits = {(a["reference_id"], a["amount"]) for a in adjustments
               if a["kind"] == "CHARGEBACK_CREDIT"}
    for a in adjustments:
        if a["kind"] != "CHARGEBACK_DEBIT" or a.get("outcome") != "WON":
            continue
        if (a["reference_id"], a["amount"]) in credits:
            continue
        posted = date.fromisoformat(a["posted_date"])
        age = (as_of - posted).days
        if age > CHARGEBACK_RECREDIT_SLA_DAYS:
            out.add("CHARGEBACK_NOT_RECREDITED", a["adjustment_id"],
                    Money.from_rupees_str(a["amount"]),
                    f"dispute {a['adjustment_id']} on {a['reference_id']} debited Rs "
                    f"{a['amount']} on {posted}, outcome WON, no re-credit after "
                    f"{age} days (SLA {CHARGEBACK_RECREDIT_SLA_DAYS}d)",
                    reference_id=a["reference_id"], age_days=age)


def detect_reserve_not_released(adjustments, as_of: date, out: FindingSet) -> None:
    """A rolling reserve held past its release schedule.

    Not lost, but working capital held hostage. Requires the RESERVE_RELEASED
    lifecycle to exist in the data -- without it every hold looks like a leak and
    the rule is trivially satisfiable rather than a detector (see PATTERN-01).
    """
    released = {a["reference_id"] for a in adjustments if a["kind"] == "RESERVE_RELEASED"}
    for a in adjustments:
        if a["kind"] != "RESERVE_HELD" or a["reference_id"] in released:
            continue
        posted = date.fromisoformat(a["posted_date"])
        age = (as_of - posted).days
        if age > RESERVE_RELEASE_SLA_DAYS:
            out.add("RESERVE_NOT_RELEASED", a["reference_id"],
                    Money.from_rupees_str(a["amount"]),
                    f"reserve of Rs {a['amount']} held on {posted} against "
                    f"{a['reference_id']}, no release after {age} days "
                    f"(SLA {RESERVE_RELEASE_SLA_DAYS}d)",
                    age_days=age)


# =====================================================================

def run_all(*, fs, payments, refunds, adjustments, bank_rows, cascade_result,
            calendar, as_of: date, covered_cycles=None) -> FindingSet:
    out = FindingSet()
    # structural first: these carry the headline and consult no contract
    detect_missing_settlement(payments, cascade_result, calendar, as_of, out,
                              bank_rows=bank_rows, covered_cycles=covered_cycles)
    detect_duplicate_payout(bank_rows, out)
    detect_duplicate_capture(payments, out)
    detect_refund_not_reached(refunds, bank_rows, out)
    detect_chargeback_not_recredited(adjustments, as_of, out)
    detect_reserve_not_released(adjustments, as_of, out)
    # contract-dependent second, explicitly labelled
    detect_fee_overcharge(fs, payments, out)
    detect_gst_mismatch(fs, payments, out)
    detect_short_settlement(cascade_result, bank_rows, payments, refunds,
                            adjustments, fs, out)
    detect_zero_mdr_violation(fs, payments, out)
    return out
