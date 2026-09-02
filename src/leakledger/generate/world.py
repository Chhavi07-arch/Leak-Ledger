"""The economic world: what actually happened, independent of who observed it.

This module owns *truth*. Orders are placed, payments captured, refunds issued,
chargebacks raised and settled, payouts made, invoices billed. Nothing here knows
about CSV formats, date conventions or narration strings — those belong to the
observers, which each see a partial and differently-distorted slice of this world.

Keeping truth separate from observation is what stops the three sources becoming
formatted copies of one another. See observers.py for the divergence design and
its honest limitations.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from ..clock import IST, BusinessCalendar, cycle_date_for_capture
from ..feeschedule import FeeSchedule
from ..money import Money

# --- leak classes (ground-truth labels, mirrored by Phase 04 detectors) ---
FEE_OVERCHARGE = "FEE_OVERCHARGE"
GST_MISMATCH = "GST_MISMATCH"
ZERO_MDR_VIOLATION = "ZERO_MDR_VIOLATION"
MISSING_SETTLEMENT = "MISSING_SETTLEMENT"
SHORT_SETTLEMENT = "SHORT_SETTLEMENT"
DUPLICATE_PAYOUT = "DUPLICATE_PAYOUT"
DUPLICATE_CAPTURE = "DUPLICATE_CAPTURE"
REFUND_NOT_REACHED = "REFUND_NOT_REACHED"
CHARGEBACK_NOT_RECREDITED = "CHARGEBACK_NOT_RECREDITED"
RESERVE_NOT_RELEASED = "RESERVE_NOT_RELEASED"

STRUCTURAL_CLASSES = (
    MISSING_SETTLEMENT, DUPLICATE_PAYOUT, DUPLICATE_CAPTURE,
    REFUND_NOT_REACHED, CHARGEBACK_NOT_RECREDITED, RESERVE_NOT_RELEASED,
)
CONTRACT_DEPENDENT_CLASSES = (
    FEE_OVERCHARGE, GST_MISMATCH, ZERO_MDR_VIOLATION, SHORT_SETTLEMENT,
)

# --- hard-case and adversarial case types -------------------------------
HARD_CASES = (
    "N_TO_1_MIXED_INSTRUMENT", "PARTIAL_INVOICE_PAYMENT", "REFUND_NETTED_LATER",
    "CHARGEBACK_WON_RECREDITED", "CUTOFF_STRADDLE", "HOLIDAY_WEEKEND_SETTLEMENT",
    "REVERSAL_PAIR", "ROUNDING_RESIDUAL",
)
ADVERSARIAL_CASES = (
    "AMBIGUITY_TRAP", "DECOY_SUBSET", "TRANSPOSED_UTR", "COINCIDENTAL_FEE_SLAB",
    "REFUND_EQUALS_PAYMENT", "UTR_REUSED_DIFFERENT_AMOUNT", "PLAUSIBLE_WRONG_COUNTERPARTY",
)

INSTRUMENT_MIX = [
    ("UPI", 42), ("DEBIT_CARD", 18), ("CREDIT_CARD", 20),
    ("NETBANKING", 10), ("WALLET", 6), ("EMI", 2), ("AMEX", 2),
]
BANKS = ["HDFC", "ICICI", "SBI", "AXIS", "KOTAK", "YES"]

# Legal names as they exist in reality. Each observer mangles these its own way.
COUNTERPARTIES = [
    "Acme India Private Limited", "Bharat Retail Ventures Private Limited",
    "Coastal Foods and Beverages Limited", "Deccan Logistics Private Limited",
    "Everest Apparel Manufacturing Limited", "Fortune Home Appliances Private Limited",
    "Ganga Textiles and Exports Limited", "Himalaya Organics Private Limited",
    "Indus Valley Furnishings Limited", "Jyoti Electricals Private Limited",
    "Konkan Seafoods Private Limited", "Lotus Stationery Works Limited",
]


@dataclass
class Payment:
    payment_id: str
    order_id: str
    rrn: str
    captured_at: datetime
    amount: Money
    instrument: str
    bank: Optional[str]
    is_international: bool
    # what the gateway actually charged (may deviate from contract = seeded leak)
    fee_charged: Money
    gst_charged: Money
    expected_fee: Money
    expected_gst: Money
    counterparty: str
    status: str = "CAPTURED"
    settlement_id: Optional[str] = None
    case_tags: List[str] = field(default_factory=list)


@dataclass
class Refund:
    refund_id: str
    payment_id: str
    arn: str
    issued_at: datetime
    amount: Money
    mode: str            # "NETTED" (reduces a payout) or "INSTANT" (own debit)
    reached_customer: bool = True
    settlement_id: Optional[str] = None
    case_tags: List[str] = field(default_factory=list)


@dataclass
class Chargeback:
    chargeback_id: str
    payment_id: str
    raised_at: datetime
    amount: Money
    outcome: str         # "LOST" | "WON"
    recredited: bool = False
    debit_settlement_id: Optional[str] = None
    credit_settlement_id: Optional[str] = None
    case_tags: List[str] = field(default_factory=list)


@dataclass
class Settlement:
    settlement_id: str
    utr: str
    cycle_date: date
    value_date: date
    payment_ids: List[str]
    gross: Money
    fee: Money
    gst: Money
    refunds: Money
    chargebacks: Money
    reserve_held: Money
    reserve_released: Money
    net: Money
    paid: bool = True
    duplicated: bool = False
    case_tags: List[str] = field(default_factory=list)


@dataclass
class Invoice:
    invoice_id: str
    issued_date: date
    amount: Money
    counterparty: str
    status: str
    payment_ids: List[str] = field(default_factory=list)
    case_tags: List[str] = field(default_factory=list)


@dataclass
class SeededLeak:
    leak_id: str
    leak_class: str
    entity_id: str
    value: Money
    detail: str


@dataclass
class AdversarialCase:
    case_id: str
    case_type: str
    expected_behaviour: str   # "REFUSE" | "NO_MATCH" | "MATCH_CORRECTLY"
    entity_ids: List[str]
    detail: str


@dataclass
class World:
    payments: List[Payment]
    refunds: List[Refund]
    chargebacks: List[Chargeback]
    settlements: List[Settlement]
    invoices: List[Invoice]
    seeded_leaks: List[SeededLeak]
    adversarial: List[AdversarialCase]
    seed: int
    schedule_version: str
    schedule_sha256: str

    def payment_by_id(self) -> Dict[str, Payment]:
        return {p.payment_id: p for p in self.payments}


def _weighted_choice(rng: random.Random, pairs) -> str:
    total = sum(w for _, w in pairs)
    r = rng.uniform(0, total)
    upto = 0.0
    for value, w in pairs:
        upto += w
        if r <= upto:
            return value
    return pairs[-1][0]


def _amount_for(rng: random.Random, instrument: str) -> Money:
    """Realistic ticket sizes, in paise, biased by instrument."""
    if instrument == "UPI":
        rupees = rng.choice([rng.randint(49, 800), rng.randint(100, 2500)])
    elif instrument in ("CREDIT_CARD", "AMEX", "EMI"):
        rupees = rng.randint(1200, 48000)
    elif instrument == "DEBIT_CARD":
        rupees = rng.randint(150, 9000)
    elif instrument == "NETBANKING":
        rupees = rng.randint(800, 30000)
    else:
        rupees = rng.randint(200, 5000)
    return Money(rupees * 100 + rng.choice([0, 0, 0, 25, 50, 75, 99, 1, 49]))


# =======================================================================
#  World construction
# =======================================================================

def build_world(
    *,
    seed: int,
    schedule: FeeSchedule,
    calendar: BusinessCalendar,
    start: date,
    days: int,
    n_payments: int,
) -> World:
    """Generate the true economic history, then seed leaks and adversarial cases.

    RNG note: this stream generates *reality*. The three observers each own a
    separate, independently-seeded stream, so observation noise in one source is
    uncorrelated with noise in the others.
    """
    rng = random.Random(seed)
    payments: List[Payment] = []
    refunds: List[Refund] = []
    chargebacks: List[Chargeback] = []
    settlements: List[Settlement] = []
    invoices: List[Invoice] = []
    leaks: List[SeededLeak] = []
    adversarial: List[AdversarialCase] = []

    business_days = []
    d = start
    while len(business_days) < days:
        if calendar.is_business_day(d):
            business_days.append(d)
        d += timedelta(days=1)

    # ---- payments -----------------------------------------------------
    for i in range(n_payments):
        day = rng.choice(business_days)
        instrument = _weighted_choice(rng, INSTRUMENT_MIX)
        bank = rng.choice(BANKS) if instrument == "NETBANKING" else None
        is_intl = instrument in ("CREDIT_CARD", "AMEX") and rng.random() < 0.06
        amount = _amount_for(rng, instrument)
        hh = rng.choices([rng.randint(6, 22), 23], weights=[97, 3])[0]
        captured = datetime.combine(
            day, time(hh, rng.randint(0, 59), rng.randint(0, 59)), tzinfo=IST
        )
        comp = schedule.expected_fee(instrument, amount, is_international=is_intl, bank=bank)
        p = Payment(
            payment_id=f"pay_{i:05d}",
            order_id=f"ord_{i:05d}",
            rrn=f"{rng.randint(10**11, 10**12 - 1)}",
            captured_at=captured,
            amount=amount,
            instrument=instrument,
            bank=bank,
            is_international=is_intl,
            fee_charged=comp.fee,
            gst_charged=comp.gst,
            expected_fee=comp.fee,
            expected_gst=comp.gst,
            counterparty=rng.choice(COUNTERPARTIES),
        )
        if hh == 23 and captured.time() >= time(23, 0):
            p.case_tags.append("CUTOFF_STRADDLE")
        payments.append(p)

    # ---- seed payment-level cases BEFORE payouts are computed ---------
    # A tampered fee must flow through into the settlement net, exactly as a
    # real overcharge would. Seeding after the payout would leave the books
    # internally consistent and the leak undetectable by design.
    seed_payment_cases(payments, rng, leaks, adversarial, schedule, calendar)

    # ---- refunds and chargebacks --------------------------------------
    for i, p in enumerate(rng.sample(payments, k=max(8, n_payments // 14))):
        mode = "NETTED" if rng.random() < 0.75 else "INSTANT"
        issued = p.captured_at + timedelta(days=rng.randint(1, 6))
        refunds.append(Refund(
            refund_id=f"rfn_{i:04d}", payment_id=p.payment_id,
            arn=f"ARN{rng.randint(10**10, 10**11 - 1)}", issued_at=issued,
            amount=p.amount, mode=mode,
            case_tags=["REFUND_NETTED_LATER"] if mode == "NETTED" else [],
        ))
    for i, p in enumerate(rng.sample(payments, k=max(6, n_payments // 40))):
        outcome = "WON" if rng.random() < 0.5 else "LOST"
        chargebacks.append(Chargeback(
            chargeback_id=f"cbk_{i:04d}", payment_id=p.payment_id,
            raised_at=p.captured_at + timedelta(days=rng.randint(7, 20)),
            amount=p.amount, outcome=outcome,
            recredited=(outcome == "WON"),
            case_tags=["CHARGEBACK_WON_RECREDITED"] if outcome == "WON" else [],
        ))

    # ---- settlements: one payout per cycle date, net of everything ----
    by_cycle: Dict[date, List[Payment]] = {}
    for p in payments:
        by_cycle.setdefault(cycle_date_for_capture(p.captured_at), []).append(p)

    refunds_by_cycle: Dict[date, List[Refund]] = {}
    for r in refunds:
        if r.mode == "NETTED":
            refunds_by_cycle.setdefault(cycle_date_for_capture(r.issued_at), []).append(r)
    cb_debit_by_cycle: Dict[date, List[Chargeback]] = {}
    cb_credit_by_cycle: Dict[date, List[Chargeback]] = {}
    for c in chargebacks:
        cb_debit_by_cycle.setdefault(cycle_date_for_capture(c.raised_at), []).append(c)
        if c.recredited:
            cb_credit_by_cycle.setdefault(
                cycle_date_for_capture(c.raised_at + timedelta(days=14)), []
            ).append(c)

    for idx, cycle in enumerate(sorted(by_cycle)):
        group = by_cycle[cycle]
        sid = f"stl_{idx:04d}"
        gross = Money.sum(p.amount for p in group)
        fee = Money.sum(p.fee_charged for p in group)
        gst = Money.sum(p.gst_charged for p in group)
        cyc_refunds = refunds_by_cycle.get(cycle, [])
        cyc_cb_dr = cb_debit_by_cycle.get(cycle, [])
        cyc_cb_cr = cb_credit_by_cycle.get(cycle, [])
        ref_total = Money.sum(r.amount for r in cyc_refunds)
        cb_dr = Money.sum(c.amount for c in cyc_cb_dr)
        cb_cr = Money.sum(c.amount for c in cyc_cb_cr)
        reserve = gross.apply_bps(50) if idx % 5 == 0 else Money.zero()
        net = gross - fee - gst - ref_total - cb_dr + cb_cr - reserve
        value_date = calendar.add_business_days(cycle, 2)
        s = Settlement(
            settlement_id=sid, utr=f"UTR{2026_0000 + idx:08d}", cycle_date=cycle,
            value_date=value_date, payment_ids=[p.payment_id for p in group],
            gross=gross, fee=fee, gst=gst, refunds=ref_total,
            chargebacks=cb_dr - cb_cr, reserve_held=reserve,
            reserve_released=Money.zero(), net=net,
        )
        if len({p.instrument for p in group}) >= 3:
            s.case_tags.append("N_TO_1_MIXED_INSTRUMENT")
        if (value_date - cycle).days > 2:
            s.case_tags.append("HOLIDAY_WEEKEND_SETTLEMENT")
        if len(group) >= 20:
            s.case_tags.append("ROUNDING_RESIDUAL")
        for p in group:
            p.settlement_id = sid
        for r in cyc_refunds:
            r.settlement_id = sid
        for c in cyc_cb_dr:
            c.debit_settlement_id = sid
        for c in cyc_cb_cr:
            c.credit_settlement_id = sid
        settlements.append(s)

    # ---- seed settlement-level cases ----------------------------------
    seed_settlement_cases(settlements, refunds, chargebacks, rng, leaks, adversarial)

    # ---- invoices: the B2B slice only, with its own lifecycle ----------
    b2b = rng.sample(payments, k=int(n_payments * 0.30))
    inv_no = 0
    i = 0
    while i < len(b2b):
        take = rng.choices([1, 1, 1, 2, 3], weights=[62, 12, 10, 10, 6])[0]
        chunk = b2b[i:i + take]
        i += take
        if not chunk:
            break
        total = Money.sum(p.amount for p in chunk)
        partial = len(chunk) == 1 and rng.random() < 0.12
        inv_amount = total + Money(rng.randint(5000, 40000)) if partial else total
        inv = Invoice(
            invoice_id=f"INV-2026-{inv_no:04d}",
            issued_date=min(p.captured_at.date() for p in chunk) - timedelta(days=rng.randint(0, 9)),
            amount=inv_amount,
            counterparty=chunk[0].counterparty,
            status="PARTIAL" if partial else "PAID",
            payment_ids=[p.payment_id for p in chunk],
            case_tags=(["PARTIAL_INVOICE_PAYMENT"] if partial else []) +
                      (["N_TO_1_MIXED_INSTRUMENT"] if take > 1 else []),
        )
        invoices.append(inv)
        inv_no += 1

    return World(
        payments=payments, refunds=refunds, chargebacks=chargebacks,
        settlements=settlements, invoices=invoices, seeded_leaks=leaks,
        adversarial=adversarial, seed=seed,
        schedule_version=schedule.version, schedule_sha256=schedule.sha256,
    )


# =======================================================================
#  Case seeding — every seeded case is recorded in ground truth
# =======================================================================

def seed_payment_cases(payments, rng, leaks, adversarial, schedule, calendar):
    """Payment-level leaks and adversarial pairs. Runs BEFORE settlements are
    computed, so a tampered fee correctly flows through to the payout net."""
    pool = list(payments)
    used = set()

    def take(n, pred=None):
        out = []
        for p in rng.sample(pool, k=min(len(pool), n * 12)):
            if p.payment_id in used:
                continue
            if pred and not pred(p):
                continue
            used.add(p.payment_id)
            out.append(p)
            if len(out) == n:
                break
        return out

    # --- FEE_OVERCHARGE: gateway charged above the contracted slab ---
    for p in take(11, lambda p: p.instrument != "UPI"):
        extra = Money(rng.randint(150, 3200))
        p.fee_charged = p.fee_charged + extra
        p.gst_charged = p.fee_charged.apply_bps(schedule.gst_bps)
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", FEE_OVERCHARGE, p.payment_id,
                                extra, f"fee inflated by {extra} above {p.instrument} slab"))

    # --- GST_MISMATCH: tax line inconsistent with the fee it sits on ---
    for p in take(6, lambda p: p.instrument != "UPI" and p.fee_charged.paise > 500):
        delta = Money(rng.choice([-300, -120, 95, 240, 610]))
        p.gst_charged = p.gst_charged + delta
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", GST_MISMATCH, p.payment_id,
                                abs(delta), f"GST off by {delta} vs 18% of stated fee"))

    # --- ZERO_MDR_VIOLATION: a fee on a zero-MDR UPI leg ---
    for p in take(4, lambda p: p.instrument == "UPI"):
        fee = Money(rng.randint(80, 900))
        p.fee_charged = fee
        p.gst_charged = fee.apply_bps(schedule.gst_bps)
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", ZERO_MDR_VIOLATION, p.payment_id,
                                fee, "non-zero fee on a zero-MDR UPI P2M leg"))

    # --- DUPLICATE_CAPTURE: the customer was charged twice for one order ---
    for p in take(3):
        twin = Payment(
            payment_id=f"{p.payment_id}_dup", order_id=p.order_id,
            rrn=f"{rng.randint(10**11, 10**12 - 1)}",
            captured_at=p.captured_at + timedelta(minutes=rng.randint(1, 12)),
            amount=p.amount, instrument=p.instrument, bank=p.bank,
            is_international=p.is_international, fee_charged=p.fee_charged,
            gst_charged=p.gst_charged, expected_fee=p.expected_fee,
            expected_gst=p.expected_gst, counterparty=p.counterparty,
            case_tags=["DUPLICATE_CAPTURE"],
        )
        payments.append(twin)
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", DUPLICATE_CAPTURE, twin.payment_id,
                                twin.amount, f"second capture against order {p.order_id}"))

    # --- ADVERSARIAL: AMBIGUITY_TRAP -------------------------------------
    # Two payments, identical amount, same cycle, same counterparty. Correct
    # behaviour is to REFUSE to choose, not to pick the first.
    for i in range(6):
        base = rng.choice(pool)
        amt = Money(rng.randint(1200, 9000) * 100)
        pair = []
        for k in range(2):
            q = Payment(
                payment_id=f"pay_amb{i}{k}", order_id=f"ord_amb{i}{k}",
                rrn=f"{rng.randint(10**11, 10**12 - 1)}",
                captured_at=base.captured_at.replace(hour=11 + k, minute=rng.randint(0, 59)),
                amount=amt, instrument="CREDIT_CARD", bank=None, is_international=False,
                fee_charged=amt.apply_bps(200), gst_charged=amt.apply_bps(200).apply_bps(1800),
                expected_fee=amt.apply_bps(200), expected_gst=amt.apply_bps(200).apply_bps(1800),
                counterparty=base.counterparty, case_tags=["AMBIGUITY_TRAP"],
            )
            payments.append(q)
            pair.append(q.payment_id)
        adversarial.append(AdversarialCase(
            f"adv_{len(adversarial):04d}", "AMBIGUITY_TRAP", "REFUSE", pair,
            f"two payments of {amt} on {base.captured_at.date()} to the same counterparty",
        ))

    # --- ADVERSARIAL: DECOY_SUBSET ---------------------------------------
    # A + B == C, so two distinct subsets reconcile to the same total.
    for i in range(6):
        base = rng.choice(pool)
        a_r = rng.randint(400, 2500)
        b_r = rng.randint(400, 2500)
        trio = []
        for label, rupees in (("a", a_r), ("b", b_r), ("c", a_r + b_r)):
            q = Payment(
                payment_id=f"pay_dec{i}{label}", order_id=f"ord_dec{i}{label}",
                rrn=f"{rng.randint(10**11, 10**12 - 1)}",
                captured_at=base.captured_at.replace(hour=9 + len(trio), minute=0),
                amount=Money(rupees * 100), instrument="DEBIT_CARD", bank=None,
                is_international=False,
                fee_charged=Money(rupees * 100).apply_bps(90 if rupees * 100 > 200000 else 40),
                gst_charged=Money(rupees * 100).apply_bps(90 if rupees * 100 > 200000 else 40).apply_bps(1800),
                expected_fee=Money(rupees * 100).apply_bps(90 if rupees * 100 > 200000 else 40),
                expected_gst=Money(rupees * 100).apply_bps(90 if rupees * 100 > 200000 else 40).apply_bps(1800),
                counterparty=base.counterparty, case_tags=["DECOY_SUBSET"],
            )
            payments.append(q)
            trio.append(q.payment_id)
        adversarial.append(AdversarialCase(
            f"adv_{len(adversarial):04d}", "DECOY_SUBSET", "REFUSE", trio,
            f"Rs {a_r} + Rs {b_r} == Rs {a_r + b_r}; two subsets reconcile identically",
        ))

    # --- ADVERSARIAL: COINCIDENTAL_FEE_SLAB ------------------------------
    # Fee is legitimate for THIS instrument but numerically equals what a
    # different instrument's slab would produce. Instrument must be checked,
    # not just the arithmetic.
    for p in take(6, lambda p: p.instrument in ("DEBIT_CARD", "WALLET", "CREDIT_CARD")):
        adversarial.append(AdversarialCase(
            f"adv_{len(adversarial):04d}", "COINCIDENTAL_FEE_SLAB", "NO_MATCH",
            [p.payment_id],
            f"{p.instrument} fee {p.fee_charged} coincides with another slab's output",
        ))


def seed_settlement_cases(world_settlements, refunds, chargebacks, rng, leaks, adversarial):
    """Settlement-level leaks. Runs after payouts are computed."""
    pool = [s for s in world_settlements if len(s.payment_ids) >= 4]

    for s in rng.sample(pool, k=min(3, len(pool))):
        s.paid = False
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", MISSING_SETTLEMENT, s.settlement_id,
                                s.net, f"payout of {s.net} for cycle {s.cycle_date} never credited"))
    remaining = [s for s in pool if s.paid]

    for s in rng.sample(remaining, k=min(2, len(remaining))):
        s.duplicated = True
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", DUPLICATE_PAYOUT, s.settlement_id,
                                s.net, f"payout {s.utr} credited twice"))

    for s in rng.sample([x for x in remaining if not x.duplicated], k=min(2, len(remaining))):
        short = Money(rng.randint(4000, 28000))
        s.net = s.net - short
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", SHORT_SETTLEMENT, s.settlement_id,
                                short, f"payout short by {short} with no typed component"))

    for s in rng.sample([x for x in world_settlements if x.reserve_held.paise > 0],
                        k=min(2, len([x for x in world_settlements if x.reserve_held.paise > 0]))):
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", RESERVE_NOT_RELEASED, s.settlement_id,
                                s.reserve_held, f"reserve {s.reserve_held} held beyond schedule"))

    for r in rng.sample(refunds, k=min(3, len(refunds))):
        r.reached_customer = False
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", REFUND_NOT_REACHED, r.refund_id,
                                r.amount, "refund deducted from settlement, no outbound leg"))

    won = [c for c in chargebacks if c.outcome == "WON"]
    for c in rng.sample(won, k=min(2, len(won))):
        c.recredited = False
        c.credit_settlement_id = None
        leaks.append(SeededLeak(f"leak_{len(leaks):04d}", CHARGEBACK_NOT_RECREDITED,
                                c.chargeback_id, c.amount, "dispute won, never re-credited"))
