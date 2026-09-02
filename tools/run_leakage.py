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
from leakledger.cascade.engine import Cascade, covered_cycles_by_matching  # noqa: E402
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
    covered = covered_cycles_by_matching(engine, bank)
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
