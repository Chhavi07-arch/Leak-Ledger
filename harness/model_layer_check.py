#!/usr/bin/env python3
"""PLAN.md Phase 06 gate: re-run the harness with the model layer ON.

  "Re-run the full harness with the model layer on. If false-match rate moved at
   all, the boundary is leaking; find it."

Run with the ADVERSARIAL provider, not a live one. A live model would test
whatever mistakes it happens to make today; the adversarial provider tests the
mistakes that would do the most damage, every time, deterministically. If the
numbers are identical with a maximally-wrong model wired in, the boundary holds.
"""
from __future__ import annotations
import csv, json, sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                                 # noqa: E402
from leakledger.feeschedule import FeeSchedule                                # noqa: E402
from leakledger.money import Money                                            # noqa: E402
from leakledger.schema import ingest_rows                                     # noqa: E402
from leakledger.ai.provider import AdversarialProvider                        # noqa: E402
from leakledger.cascade.engine import (                                       # noqa: E402
    AUTO_APPLY, REVIEW, Cascade, covered_cycles_by_matching)
from leakledger.leakage import detectors                                      # noqa: E402

DATA = ROOT / "data" / "generated"
def _load(n):
    """Read a generated CSV. Uses a context manager so the handle is closed --
    the lambda this replaced leaked one per call and filled test runs with
    ResourceWarnings."""
    with (DATA / n).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run(provider):
    fs = FeeSchedule.from_file(ROOT / "config" / "fee_schedule.v1.json")
    cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
    gw = ingest_rows("gateway", _load("gateway_payments.csv"))
    refunds, adj = _load("gateway_refunds.csv"), _load("gateway_adjustments.csv")
    bank = _load("bank_statement.csv")
    eng = Cascade(payments=gw.records, refunds=refunds, bank=bank, adjustments=adj,
                  calendar=cal, model_provider=provider)
    casc = eng.run()
    cov = covered_cycles_by_matching(eng, bank)
    found = detectors.run_all(fs=fs, payments=gw.records, refunds=refunds, adjustments=adj,
                              bank_rows=bank, cascade_result=casc, calendar=cal,
                              as_of=date(2026, 7, 31), covered_cycles=cov)
    truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
    by_net = {}
    for s in truth["settlements"]:
        if s["paid"]:
            by_net.setdefault(s["net_paise"], []).append(s)
    correct = wrong = 0
    for m in casc.matches:
        if m.tier != "T3" or m.disposition not in (AUTO_APPLY, REVIEW):
            continue
        b = next(x for x in bank if x["txn_id"] == m.bank_txn_id)
        cands = by_net.get(Money.from_rupees_str(b["amount"]).paise, [])
        if len(cands) != 1:
            continue
        if set(cands[0]["payment_ids"]) == set(m.matched_ids):
            correct += 1
        else:
            wrong += 1
    return {
        "false_match_rate": wrong / (correct + wrong) if correct + wrong else None,
        "correct": correct, "wrong": wrong,
        "tiers": dict(sorted(casc.by_tier().items())),
        "reasons": dict(sorted(Counter(
            m.reason_code for m in casc.matches if m.reason_code).items())),
        "findings": len(found.findings),
        "headline_paise": found.total().paise,
    }


def main() -> int:
    off = run(None)
    prov = AdversarialProvider(
        counterparties=["ACME PVT LTD", "BHARAT RETAIL VENTURES", "DECCAN LOGISTIC"],
        references=["UTR20260002", "ARN99999999999", "UTR00000000"])
    on = run(prov)
    print(f"{'metric':22} {'model OFF':>26} {'model ON (adversarial)':>26}")
    for k in ("false_match_rate", "correct", "wrong", "findings", "headline_paise"):
        print(f"{k:22} {str(off[k]):>26} {str(on[k]):>26}")
    print(f"{'tiers':22} {str(off['tiers']):>26} {str(on['tiers']):>26}")
    same = all(off[k] == on[k] for k in
               ("false_match_rate", "correct", "wrong", "findings", "headline_paise"))
    print()
    if same:
        print("BOUNDARY HOLDS — every metric identical with a maximally-wrong model wired in.")
        return 0
    print("BOUNDARY LEAKS — a metric moved. Stop and find it before reporting anything else.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
