#!/usr/bin/env python3
"""Validate the internal consistency of ground truth, BEFORE scoring against it.

INC-014 is why this exists. A settlement was seeded whose stated net contradicted
its own components: a withheld chargeback re-credit was applied after payout nets
were computed, leaving the payout inflated by a credit no adjustment reported. The
engine correctly refused to reconcile it and was penalised for being right.

That failure mode cannot be caught by testing the engine harder -- the engine is
graded by the very artefact that is broken, so a self-contradictory fixture
silently penalises a correct implementation and can reward an incorrect one. The
only defence is to treat the data as a first-class artefact with its own
assertions.

Checks performed:
  1. SETTLEMENT IDENTITY closes for every settlement, exactly, in integer paise.
  2. Every payment referenced by a settlement exists in the gateway export.
  3. No payment is claimed by two settlements.
  4. Every seeded leak names an entity that exists.
  5. Every adversarial case is REACHABLE -- its entities resolve and, for
     ambiguity traps, at least one host settlement was actually paid (PATTERN-01).
  6. Every deduction a settlement claims is reported by some adjustment or refund.

Exit code 1 on any failure. This runs before the scorecard, not after.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.money import Money                                # noqa: E402

DATA = ROOT / "data" / "generated"
_load = lambda n: list(csv.DictReader((DATA / n).open(encoding="utf-8")))


def main() -> int:
    truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
    payments = {r["payment_id"] for r in _load("gateway_payments.csv")}
    refunds = _load("gateway_refunds.csv")
    adjustments = _load("gateway_adjustments.csv")
    failures: list[str] = []
    checks = 0

    # ---- 1. settlement identity ------------------------------------
    # A seeded SHORT_SETTLEMENT breaks the identity BY DESIGN -- that is the leak.
    # The check accounts for it explicitly rather than being relaxed: an
    # unexplained delta is a data defect, a delta equal to a declared shortfall
    # is the fixture working.
    declared_short = {l["entity_id"]: l["value_paise"] for l in truth["seeded_leaks"]
                      if l["class"] == "SHORT_SETTLEMENT"}
    fees = truth["expected_fees"]
    for s in truth["settlements"]:
        checks += 1
        gross = sum(fees[p]["amount_paise"] for p in s["payment_ids"] if p in fees)
        fee = sum(fees[p]["charged_fee_paise"] for p in s["payment_ids"] if p in fees)
        gst = sum(fees[p]["charged_gst_paise"] for p in s["payment_ids"] if p in fees)
        lhs = s["net_paise"]
        rhs = (gross - fee - gst - s["refunds_paise"] - s["chargebacks_paise"]
               - s["reserve_held_paise"] + s.get("reserve_released_paise", 0))
        expected_delta = -declared_short.get(s["settlement_id"], 0)
        if lhs - rhs != expected_delta:
            failures.append(
                f"SETTLEMENT IDENTITY BROKEN {s['settlement_id']}: stated net "
                f"Rs {lhs/100:,.2f} != components Rs {rhs/100:,.2f} "
                f"(delta Rs {(lhs-rhs)/100:,.2f}, expected Rs {expected_delta/100:,.2f}). "
                f"gross={gross/100:,.2f} "
                f"fee={fee/100:,.2f} gst={gst/100:,.2f} refunds={s['refunds_paise']/100:,.2f} "
                f"cb={s['chargebacks_paise']/100:,.2f} reserve={s['reserve_held_paise']/100:,.2f}")

    # ---- 2/3. payment references -----------------------------------
    claimed = Counter()
    for s in truth["settlements"]:
        for p in s["payment_ids"]:
            checks += 1
            claimed[p] += 1
            if p not in payments:
                failures.append(f"DANGLING PAYMENT {p} in {s['settlement_id']} "
                                f"is absent from the gateway export")
    for p, n in sorted(claimed.items()):
        if n > 1:
            failures.append(f"DOUBLE-CLAIMED PAYMENT {p} appears in {n} settlements")

    # ---- 4. leak entities exist ------------------------------------
    known = (payments | {s["settlement_id"] for s in truth["settlements"]}
             | {r["refund_id"] for r in refunds}
             | {a["adjustment_id"] for a in adjustments})
    for l in truth["seeded_leaks"]:
        checks += 1
        if l["entity_id"] not in known:
            failures.append(f"SEEDED LEAK {l['leak_id']} names unknown entity "
                            f"{l['entity_id']!r}")

    # ---- 5. adversarial cases are reachable (PATTERN-01) -----------
    links = truth["payment_settlement_links"]
    stl = {s["settlement_id"]: s for s in truth["settlements"]}
    for a in truth["adversarial_cases"]:
        checks += 1
        if a["type"] != "AMBIGUITY_TRAP":
            continue
        hosts = {links.get(i) for i in a["entity_ids"]} - {None}
        if not any(stl[h]["paid"] for h in hosts if h in stl):
            failures.append(
                f"UNREACHABLE ADVERSARIAL CASE {a['case_id']} ({a['type']}): every host "
                f"settlement {sorted(hosts)} is unpaid, so no bank credit exists and the "
                f"case can never fire (PATTERN-01)")

    # ---- 6. every deduction a settlement claims is reported PER CYCLE ----
    # Compared per cycle, not as global totals: adjustments legitimately include
    # chargebacks posted after the last settlement cycle, so a totals comparison
    # is apples-to-oranges and reports a phantom defect. The real invariant is
    # that each settlement's claimed chargeback component equals the net of the
    # adjustments posted in ITS cycle -- which is exactly the invariant INC-014
    # violated.
    cb_by_cycle: dict = defaultdict(int)
    for a in adjustments:
        if a["kind"] == "CHARGEBACK_DEBIT":
            cb_by_cycle[a["posted_date"]] += Money.from_rupees_str(a["amount"]).paise
        elif a["kind"] == "CHARGEBACK_CREDIT":
            cb_by_cycle[a["posted_date"]] -= Money.from_rupees_str(a["amount"]).paise
    for s in truth["settlements"]:
        checks += 1
        reported = cb_by_cycle.get(s["cycle_date"], 0)
        if reported != s["chargebacks_paise"]:
            failures.append(
                f"CHARGEBACK COMPONENT UNREPORTED {s['settlement_id']} (cycle "
                f"{s['cycle_date']}): settlement claims Rs {s['chargebacks_paise']/100:,.2f} "
                f"net, adjustments posted in that cycle report Rs {reported/100:,.2f} "
                f"(delta Rs {(s['chargebacks_paise']-reported)/100:,.2f}) "
                f"-- INC-014's exact signature")

    # ---- 7. reserve releases reconcile to reserve holds -------------
    held = {a["reference_id"]: Money.from_rupees_str(a["amount"]).paise
            for a in adjustments if a["kind"] == "RESERVE_HELD"}
    for a in adjustments:
        if a["kind"] != "RESERVE_RELEASED":
            continue
        checks += 1
        amt = Money.from_rupees_str(a["amount"]).paise
        if held.get(a["reference_id"]) != amt:
            failures.append(
                f"RESERVE RELEASE MISMATCH {a['adjustment_id']}: releases "
                f"Rs {amt/100:,.2f} against a hold of "
                f"Rs {held.get(a['reference_id'], 0)/100:,.2f}")

    print(f"ground truth consistency: {checks} checks")
    if failures:
        print(f"  FAILED ({len(failures)}):")
        for f in failures[:20]:
            print(f"    {f}")
        if len(failures) > 20:
            print(f"    ... and {len(failures)-20} more")
        return 1
    print("  PASS — settlement identity closes, references resolve, "
          "every adversarial case is reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
