#!/usr/bin/env python3
"""Run the cascade then the ten detectors, and report findings against ground truth."""
from __future__ import annotations
import csv, json, sys, time
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                      # noqa: E402
from leakledger.feeschedule import FeeSchedule                     # noqa: E402
from leakledger.money import Money                                 # noqa: E402
from leakledger.schema import ingest_rows                          # noqa: E402
from leakledger.cascade.engine import Cascade                      # noqa: E402
from leakledger.leakage import detectors                           # noqa: E402
from leakledger.leakage.findings import (                          # noqa: E402
    CONTRACT_DEPENDENT, RULE_CHECK, STRUCTURAL,
)

DATA = ROOT / "data" / "generated"
load = lambda n: list(csv.DictReader((DATA / n).open(encoding="utf-8")))
AS_OF = date(2026, 7, 31)


def main():
    fs = FeeSchedule.from_file(ROOT / "config" / "fee_schedule.v1.json")
    cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
    gw = ingest_rows("gateway", load("gateway_payments.csv"))
    refunds, adjustments = load("gateway_refunds.csv"), load("gateway_adjustments.csv")
    bank = load("bank_statement.csv")

    t0 = time.perf_counter()
    engine = Cascade(payments=gw.records, refunds=refunds, bank=bank,
                     adjustments=adjustments, calendar=cal)
    casc = engine.run()
    # Coverage uses the ZERO-LAG inversion deliberately. Claiming a payout is
    # missing is a strong assertion, so it is made only against the tightest
    # possible reading of which cycles a credit could belong to. Widening the
    # tolerance here would suppress the claim entirely (measured: 1 of 3 unpaid
    # cycles detectable at tolerance 0, and still 1 of 3 at tolerance 2 -- the
    # other two are shadowed by neighbouring credits and are NOT provable).
    from datetime import datetime as _dt
    saved = engine.POSTING_LAG_TOLERANCE_DAYS
    engine.POSTING_LAG_TOLERANCE_DAYS = 0
    attributed = set()          # cycles a successful match positively claimed
    for m in casc.matches:
        if m.disposition in ("AUTO_APPLY", "REVIEW") and "cycle " in m.evidence:
            try:
                attributed.add(_dt.strptime(
                    m.evidence.split("cycle ")[1].split(",")[0].strip(), "%Y-%m-%d").date())
            except ValueError:
                pass
    matched_txns = {m.bank_txn_id for m in casc.matches
                    if m.disposition in ("AUTO_APPLY", "REVIEW")}
    covered = set(attributed)
    for b in bank:                                   # credits still unspoken-for
        if b["direction"] == "CR" and b["txn_id"] not in matched_txns:
            covered.update(engine._candidate_cycles(
                _dt.strptime(b["value_date"], "%d-%m-%Y").date()))
    engine.POSTING_LAG_TOLERANCE_DAYS = saved
    t1 = time.perf_counter()
    found = detectors.run_all(fs=fs, payments=gw.records, refunds=refunds,
                              adjustments=adjustments, bank_rows=bank,
                              cascade_result=casc, calendar=cal, as_of=AS_OF,
                              covered_cycles=covered)
    t2 = time.perf_counter()

    truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
    seeded = Counter(l["class"] for l in truth["seeded_leaks"])

    print(f"cascade {t1-t0:.2f}s   detectors {t2-t1:.2f}s   "
          f"{len(gw.records)} payments, {len(bank)} bank rows")
    print()
    print(f"{'class':28} {'cat':18} {'found':>6} {'seeded':>7} {'value':>15}")
    for cat, label in ((STRUCTURAL, "STRUCTURAL"), (CONTRACT_DEPENDENT, "CONTRACT-DEP"),
                       (RULE_CHECK, "RULE CHECK")):
        for cls, fl in sorted(found.by_class().items()):
            if fl[0].category != cat:
                continue
            v = Money.sum(f.value for f in fl)
            print(f"{cls:28} {label:18} {len(fl):>6} {seeded.get(cls,0):>7} "
                  f"Rs {v.to_rupees_str():>12}")
    print()
    for cat, label in ((STRUCTURAL, "structural"), (CONTRACT_DEPENDENT, "contract-dependent"),
                       (RULE_CHECK, "rule check")):
        fl = found.by_category(cat)
        print(f"  {label:20} {len(fl):>3} findings  Rs {found.total(cat).to_rupees_str():>13}")
    print(f"  {'TOTAL':20} {len(found.findings):>3} findings  Rs {found.total().to_rupees_str():>13}")
    print()
    print("SAMPLE DERIVATIONS (each must be re-derivable by hand):")
    seen = set()
    for f in found.findings:
        if f.leak_class in seen:
            continue
        seen.add(f.leak_class)
        print(f"  {f.line()[:150]}")
    return found


if __name__ == "__main__":
    main()
