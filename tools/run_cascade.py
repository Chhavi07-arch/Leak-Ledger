#!/usr/bin/env python3
"""Run the cascade over the generated batch and report what actually happened."""
from __future__ import annotations
import csv, json, sys, time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                     # noqa: E402
from leakledger.cascade.engine import Cascade, AUTO_APPLY, REVIEW, EXCEPTION  # noqa: E402
from leakledger.schema import ingest_rows                         # noqa: E402

DATA = ROOT / "data" / "generated"


def load(name):
    with (DATA / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main():
    gw = ingest_rows("gateway", load("gateway_payments.csv"))
    bank_rows = load("bank_statement.csv")
    refunds = load("gateway_refunds.csv")
    adjustments = load("gateway_adjustments.csv")
    cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")

    print(f"ingest: {gw.summary()}")
    t0 = time.perf_counter()
    casc = Cascade(payments=gw.records, refunds=refunds, bank=bank_rows,
                   adjustments=adjustments, calendar=cal)
    res = casc.run()
    elapsed = time.perf_counter() - t0

    n = len(res.matches)
    auto = len(res.by_disposition(AUTO_APPLY))
    rev = len(res.by_disposition(REVIEW))
    exc = len(res.by_disposition(EXCEPTION))
    print()
    print(f"CASCADE over {n} bank rows in {elapsed:.2f}s "
          f"({n/elapsed:.0f} rows/s, {len(gw.records)} payments in pool)")
    print(f"  auto-apply : {auto:3}  ({100*auto/n:5.1f}%)")
    print(f"  review     : {rev:3}  ({100*rev/n:5.1f}%)")
    print(f"  exception  : {exc:3}  ({100*exc/n:5.1f}%)")
    print()
    print("  by tier   :", dict(sorted(res.by_tier().items())))
    print("  by reason :", dict(sorted(res.by_reason().items())))
    print()
    print("EXCEPTIONS (typed, every one):")
    for m in res.by_disposition(EXCEPTION)[:14]:
        print(f"  [{m.reason_code:24}] {m.bank_txn_id}  {m.evidence[:78]}")
    tot = sum(m.combinations_examined for m in res.matches)
    print()
    print(f"  total combinations examined: {tot:,}")
    return res


if __name__ == "__main__":
    main()
