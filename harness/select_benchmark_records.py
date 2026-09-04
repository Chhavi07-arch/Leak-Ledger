#!/usr/bin/env python3
"""Freeze the 50-record benchmark sample BEFORE any model is run.

The selection rule is fixed here, committed, and hashed. This exists so the
sample cannot be accused of having been chosen after seeing which records make
the LLM look bad -- the commit that creates selection.json necessarily precedes
the commit that records any benchmark result.

SELECTION RULE, stated in full:

  Population : every bank credit the DETERMINISTIC CASCADE actually resolved,
               i.e. disposition in {AUTO_APPLY, REVIEW}. Records the cascade
               refused are excluded, because a matcher cannot be scored against a
               ground truth the reference implementation itself declines to
               assert.
  Ordering   : ascending by bank txn_id -- a stable, content-independent key that
               has no relationship to difficulty, amount, tier or outcome.
  Size       : the first 50 by that ordering. If the population is smaller than
               50, ALL of it is taken and the shortfall is recorded, rather than
               padding the sample with refused records to reach a round number.

No stratification, no difficulty balancing, no sampling by tier. Any of those
would require a judgement about which records are hard, and that judgement is
precisely what the benchmark is supposed to measure rather than assume.
"""
from __future__ import annotations

import csv, hashlib, json, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                                  # noqa: E402
from leakledger.schema import ingest_rows                                      # noqa: E402
from leakledger.cascade.engine import AUTO_APPLY, REVIEW, Cascade              # noqa: E402

DATA = ROOT / "data" / "generated"
OUT = ROOT / "harness" / "benchmark_selection.json"
N = 50
def _load(n):
    """Read a generated CSV. Uses a context manager so the handle is closed --
    the lambda this replaced leaked one per call and filled test runs with
    ResourceWarnings."""
    with (DATA / n).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    gw = ingest_rows("gateway", _load("gateway_payments.csv"))
    bank = _load("bank_statement.csv")
    cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
    casc = Cascade(payments=gw.records, refunds=_load("gateway_refunds.csv"),
                   bank=bank, adjustments=_load("gateway_adjustments.csv"),
                   calendar=cal).run()

    resolved = sorted((m for m in casc.matches if m.disposition in (AUTO_APPLY, REVIEW)),
                      key=lambda m: m.bank_txn_id)
    chosen = resolved[:N]

    payload = {
        "rule": "cascade-resolved bank credits, ascending by txn_id, first 50",
        "population_size": len(resolved),
        "requested": N,
        "selected": len(chosen),
        "shortfall": max(0, N - len(resolved)),
        "records": [
            {"bank_txn_id": m.bank_txn_id, "tier": m.tier, "disposition": m.disposition,
             "matched_ids": sorted(m.matched_ids)}
            for m in chosen
        ],
        "source_hashes": {
            f: hashlib.sha256((DATA / f).read_bytes()).hexdigest()
            for f in ("gateway_payments.csv", "bank_statement.csv",
                      "gateway_refunds.csv", "gateway_adjustments.csv")
        },
    }
    body = json.dumps(payload, indent=2, sort_keys=True)
    payload["selection_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"population (cascade-resolved) : {len(resolved)}")
    print(f"selected                      : {len(chosen)}")
    if payload["shortfall"]:
        print(f"SHORTFALL                     : {payload['shortfall']} "
              f"(population smaller than {N}; NOT padded with refused records)")
    print(f"selection sha256              : {payload['selection_sha256'][:16]}")
    print(f"written                       : {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
