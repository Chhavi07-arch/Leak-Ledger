"""Three observers of one world, each seeing a different slice through a
different lens.

DIVERGENCE DESIGN — the point of this module.

The failure mode this is built to avoid is generating one canonical record and
emitting it three times with different column headers. That produces sources that
agree perfectly, a naive matcher that resolves ~100%, and a match rate that means
nothing. Four things keep these sources genuinely apart:

1.  DIFFERENT VISIBILITY, not different formatting. The bank cannot see payments
    at all — only net money movement into the account. There is no bank row whose
    amount equals a gateway payment's amount, because a payout is a batch net of
    fees, tax, refunds, chargebacks and reserve. This is the structural reason
    N:1 subset-sum has to exist, and it is real rather than injected.
2.  INDEPENDENT RNG STREAMS. Each observer owns a stream seeded separately from
    the world's and from each other's, so noise in one source is uncorrelated
    with noise in another.
3.  INDEPENDENT MANGLING OF SHARED FACTS. Both the bank and the ERP refer to the
    same legal entity, but each derives its rendering from the canonical legal
    name by its own process — the bank by uppercasing, abbreviating and truncating
    to a fixed field width; the ERP by imitating how a human types a name into a
    form. Neither derives from the other's version.
4.  DIFFERENT CONVENTIONS. Date formats, identifier namespaces (RRN vs UTR vs
    ARN), and rounding behaviour differ per source because the real systems that
    emit them were built by different organisations.

HONEST LIMITATION. All three observers were still written by one author, so the
*kinds* of divergence present are the kinds that author thought of. Independent
streams guarantee the noise is uncorrelated; they cannot guarantee it is
representative. Real-world reconciliation is hard partly because of failure modes
nobody anticipated, and no self-authored generator can reproduce that. This is
recorded in DECISIONS.md (ADR-002) rather than left implicit.
"""
from __future__ import annotations

import random
from datetime import timedelta
from typing import Dict, List

from ..money import Money
from .world import World

# --- how the bank's statement renders a counterparty --------------------
_BANK_ABBREV = [
    ("PRIVATE LIMITED", "PVT LTD"), ("LIMITED", "LTD"), ("AND", "&"),
    ("MANUFACTURING", "MFG"), ("LOGISTICS", "LOGISTIC"), ("EXPORTS", "EXP"),
]
_BANK_NARRATION_TEMPLATES = [
    "NEFT CR-{utr}-{cp}",
    "NEFT-{utr}-{cp}",
    "IMPS/P2A/{ref}/{cp}",
    "RTGS CR {utr} {cp}",
    "UPI/CR/{ref}/{cp}",
    "ACH C- {cp} {utr}",
]
_BANK_FIELD_WIDTH = 35          # statements truncate; information is genuinely lost

# --- how a human types a name into the ERP ------------------------------
_ERP_SUFFIX_VARIANTS = [
    ("Private Limited", "Pvt Ltd"), ("Private Limited", "Pvt. Ltd."),
    ("Private Limited", "PVT LTD"), ("Private Limited", "Pvt Ltd."),
    ("Limited", "Ltd"), ("Limited", "Ltd."), ("Limited", "LTD"),
]


def _bank_render_counterparty(name: str, rng: random.Random) -> str:
    s = name.upper()
    for full, short in _BANK_ABBREV:
        if rng.random() < 0.8:
            s = s.replace(full, short)
    return s


def _erp_render_counterparty(name: str, rng: random.Random) -> str:
    s = name
    for full, variant in _ERP_SUFFIX_VARIANTS:
        if s.endswith(full) and rng.random() < 0.55:
            s = s[: -len(full)] + variant
            break
    if rng.random() < 0.10:
        s = s.replace(" and ", " & ")
    if rng.random() < 0.06:
        s = s.upper()
    if rng.random() < 0.05:
        s = "  " + s + " "        # stray whitespace from a paste
    return s


# =======================================================================
#  Gateway observer — payment-level, ISO dates, its own identifiers
# =======================================================================

def observe_gateway(world: World, seed: int) -> List[Dict[str, str]]:
    rng = random.Random(seed)
    rows: List[Dict[str, str]] = []
    for p in world.payments:
        row = {
            "payment_id": p.payment_id,
            "order_id": p.order_id,
            # the gateway loses the RRN on a small share of rows
            "rrn": "" if rng.random() < 0.03 else p.rrn,
            "captured_at": p.captured_at.isoformat(),      # ISO-8601 with +05:30
            "amount": p.amount.to_rupees_str(),
            "instrument": p.instrument,
            "bank": p.bank or "",
            "is_international": "true" if p.is_international else "false",
            "status": p.status,
            "fee_charged": p.fee_charged.to_rupees_str(),
            "gst_charged": p.gst_charged.to_rupees_str(),
        }
        rows.append(row)
        # duplicate export line: the same payment appears twice in the file
        if rng.random() < 0.004:
            rows.append(dict(row))
    rng.shuffle(rows)
    return rows


# =======================================================================
#  Bank observer — settlement-level only. Cannot see a payment.
# =======================================================================

def observe_bank(world: World, seed: int, adversarial=None) -> List[Dict[str, str]]:
    """Observation-level adversarial cases are seeded HERE, not in the world.

    A transposed UTR, a reused reference or a narration naming the wrong entity
    are errors of *observation* — the economic event was fine, the record of it
    is not. Seeding them in world.py would misplace them and would also make
    them correlate with the gateway's noise, which is exactly what independent
    streams are meant to prevent.
    """
    rng = random.Random(seed)
    rows: List[Dict[str, str]] = []
    n = 0

    def emit(value_date, amount: Money, direction: str, utr: str, counterparty: str):
        nonlocal n
        n += 1
        cp = _bank_render_counterparty(counterparty, rng)
        template = rng.choice(_BANK_NARRATION_TEMPLATES)
        narration = template.format(
            utr=utr, cp=cp, ref=f"{rng.randint(10**11, 10**12 - 1)}"
        )[:_BANK_FIELD_WIDTH]                       # hard truncation, information lost
        # posting lag: the bank's own value-dating, uncorrelated with the gateway
        vd = value_date + timedelta(days=1) if rng.random() < 0.05 else value_date
        rows.append({
            "txn_id": f"BNK{n:06d}",
            "value_date": vd.strftime("%d-%m-%Y"),   # DD-MM-YYYY, not ISO
            "amount": amount.to_rupees_str(),
            "direction": direction,
            # the structured UTR field is often blank; it survives only in narration
            "utr": "" if rng.random() < 0.35 else utr,
            "narration": narration,
        })

    for s in world.settlements:
        if not s.paid:
            continue                                  # MISSING_SETTLEMENT seeds land here
        emit(s.value_date, s.net, "CR", s.utr, "Razorpay Software Private Limited")
        if s.duplicated:
            emit(s.value_date, s.net, "CR", s.utr, "Razorpay Software Private Limited")

    for r in world.refunds:
        if r.mode == "INSTANT" and r.reached_customer:
            emit(r.issued_at.date(), r.amount, "DR", r.arn,
                 world.payment_by_id()[r.payment_id].counterparty)

    adv = adversarial if adversarial is not None else []

    def _add(case_type, behaviour, ids, detail):
        from .world import AdversarialCase
        adv.append(AdversarialCase(
            f"adv_{len(adv):04d}", case_type, behaviour, ids, detail))

    credits = [r for r in rows if r["direction"] == "CR" and r["utr"]]

    # --- TRANSPOSED_UTR: two digits swapped. Must NOT fuzzy-match through. ---
    for row in rng.sample(credits, k=min(6, len(credits))):
        u = row["utr"]
        i = rng.randint(3, len(u) - 2)
        if u[i] != u[i + 1]:
            row["utr"] = u[:i] + u[i + 1] + u[i] + u[i + 2:]
            _add("TRANSPOSED_UTR", "NO_MATCH", [row["txn_id"]],
                 f"{u} recorded as {row['utr']}; near-miss reference must not resolve")

    # --- UTR_REUSED_DIFFERENT_AMOUNT: exact key must not be trusted blindly ---
    for row in rng.sample([r for r in rows if r["utr"]], k=min(5, len(rows))):
        clone = dict(row)
        clone["txn_id"] = row["txn_id"] + "X"
        clone["amount"] = (Money.from_rupees_str(row["amount"]) + Money(rng.randint(9000, 90000))).to_rupees_str()
        rows.append(clone)
        _add("UTR_REUSED_DIFFERENT_AMOUNT", "REFUSE", [row["txn_id"], clone["txn_id"]],
             f"UTR {row['utr']} appears with two different amounts")

    # --- REFUND_EQUALS_PAYMENT: sign and direction must be honoured ---
    pays = rng.sample(world.payments, k=min(5, len(world.payments)))
    for p in pays:
        n += 1
        rows.append({
            "txn_id": f"BNK{n:06d}",
            "value_date": p.captured_at.date().strftime("%d-%m-%Y"),
            "amount": p.amount.to_rupees_str(),
            "direction": "DR",
            "utr": "",
            "narration": _bank_render_counterparty(p.counterparty, rng)[:_BANK_FIELD_WIDTH],
        })
        _add("REFUND_EQUALS_PAYMENT", "NO_MATCH", [p.payment_id],
             f"a debit of {p.amount} equals unrelated payment {p.payment_id}")

    # --- PLAUSIBLE_WRONG_COUNTERPARTY: LLM proposal must be arithmetic-checked ---
    # sorted(), not set-iteration: Python randomises string hashes per process,
    # so iterating a set of strings gives a different order in every run and
    # rng.choice() over it silently breaks determinism. See INC-002.
    others = sorted({p.counterparty for p in world.payments})
    for row in rng.sample(credits, k=min(6, len(credits))):
        wrong = rng.choice(others)
        row["narration"] = f"NEFT CR-{row['utr']}-{_bank_render_counterparty(wrong, rng)}"[:_BANK_FIELD_WIDTH]
        _add("PLAUSIBLE_WRONG_COUNTERPARTY", "NO_MATCH", [row["txn_id"]],
             f"narration names {wrong!r}, which is plausible but wrong")

    rng.shuffle(rows)
    return rows


# =======================================================================
#  ERP observer — invoice-level, human-typed names, rupee rounding
# =======================================================================

def observe_erp(world: World, seed: int) -> List[Dict[str, str]]:
    rng = random.Random(seed)
    rows: List[Dict[str, str]] = []
    for inv in world.invoices:
        amount = inv.amount
        # some ERP configurations post to the nearest rupee
        if rng.random() < 0.18:
            amount = Money(round(amount.paise / 100) * 100)
        status = inv.status
        if rng.random() < 0.07:
            status = "OPEN"                            # stale status, never updated
        row = {
            "invoice_id": inv.invoice_id,
            "issued_date": inv.issued_date.isoformat(),   # ISO date, no time at all
            "amount": amount.to_rupees_str(),
            "counterparty": _erp_render_counterparty(inv.counterparty, rng),
            "status": status,
        }
        rows.append(row)
        if rng.random() < 0.02:
            dup = dict(row)
            dup["invoice_id"] = inv.invoice_id + "-A"    # re-keyed duplicate entry
            rows.append(dup)
    rng.shuffle(rows)
    return rows


# =======================================================================
#  Gateway refunds export — a separate report, as gateways actually emit
# =======================================================================

def observe_gateway_refunds(world: World, seed: int) -> List[Dict[str, str]]:
    """Refund-level export carrying ARNs.

    This is what gives T1 (exact reference) and T2 (amount + window + uniqueness)
    real work: an instant refund appears as a bank debit carrying the same ARN,
    so the two can be matched by reference. It does not violate ADR-003 — a
    refunds report links refunds to payments, never payments to payouts, so the
    gateway's account of what it settled is still not being trusted.
    """
    rng = random.Random(seed)
    rows: List[Dict[str, str]] = []
    for r in world.refunds:
        rows.append({
            "refund_id": r.refund_id,
            "payment_id": r.payment_id,
            # the export loses the ARN on a minority of rows, forcing those to T2
            "arn": "" if rng.random() < 0.18 else r.arn,
            "issued_at": r.issued_at.isoformat(),
            "amount": r.amount.to_rupees_str(),
            "mode": r.mode,
        })
    rng.shuffle(rows)
    return rows


def observe_gateway_adjustments(world: World, seed: int) -> List[Dict[str, str]]:
    """Disputes and reserve movements — the deductions that are neither fee nor tax.

    Without this the engine cannot reconcile any cycle containing a chargeback or
    a reserve hold: the payout is short by an amount it has no way to explain, and
    the search returns NO_SOLUTION before ambiguity could ever be detected. Real
    gateways publish dispute and reserve reports, so this is data a merchant
    genuinely holds. Like the refunds export it links nothing to payouts, so
    ADR-003 still holds -- the gateway's account of what it SETTLED is not trusted.
    """
    rng = random.Random(seed)
    rows: List[Dict[str, str]] = []
    for c in world.chargebacks:
        rows.append({
            "adjustment_id": c.chargeback_id, "kind": "CHARGEBACK_DEBIT",
            "reference_id": c.payment_id,
            # the cycle the settlement maths used, not a recomputation of it
            "posted_date": (c.debit_cycle or c.raised_at.date()).isoformat(),
            "amount": c.amount.to_rupees_str(), "outcome": c.outcome,
        })
        if c.recredited:
            rows.append({
                "adjustment_id": c.chargeback_id + "-CR", "kind": "CHARGEBACK_CREDIT",
                "reference_id": c.payment_id,
                "posted_date": (c.credit_cycle
                                or (c.raised_at + timedelta(days=14)).date()).isoformat(),
                "amount": c.amount.to_rupees_str(), "outcome": c.outcome,
            })
    for s in world.settlements:
        if s.reserve_held.paise:
            rows.append({
                "adjustment_id": s.settlement_id + "-RSV", "kind": "RESERVE_HELD",
                "reference_id": s.settlement_id,
                "posted_date": s.cycle_date.isoformat(),
                "amount": s.reserve_held.to_rupees_str(), "outcome": "",
            })
            # a reserve released on schedule is reported; a withheld one is not,
            # which is exactly what makes RESERVE_NOT_RELEASED detectable
            if s.reserve_release_date is not None:
                rows.append({
                    "adjustment_id": s.settlement_id + "-RSVR", "kind": "RESERVE_RELEASED",
                    "reference_id": s.settlement_id,
                    "posted_date": s.reserve_release_date.isoformat(),
                    "amount": s.reserve_held.to_rupees_str(), "outcome": "",
                })
    rng.shuffle(rows)
    return rows
