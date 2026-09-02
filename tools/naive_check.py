#!/usr/bin/env python3
"""PLAN.md Phase 02 gate: is the generated batch actually hard?

    "Write a five-line naive exact-key matcher and run it. If it resolves more
     than about 70%, the batch is too easy and every number you report
     afterwards is inflated."

CHECK 1 is that matcher, unmodified: the spreadsheet approach a finance associate
actually uses — look for a bank line with the same amount within a date window.

CHECK 2 exists because check 1 is one-sided. A batch can also fail by being hard
for the wrong reason: if the truth were unrecoverable, check 1 would read 0% and
look like a triumph. Check 2 confirms the data is still solvable by a correct
reference-level match, so a low naive score means "aggregated and noisy", not
"impossible".
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "generated"


def load(name):
    with (DATA / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    gateway = load("gateway_payments.csv")
    bank = load("bank_statement.csv")
    truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))

    # ---------- CHECK 1: the naive exact-key matcher (five lines) ----------
    bank_by_amount = defaultdict(list)
    for b in bank:
        bank_by_amount[b["amount"]].append(b)
    resolved = 0
    for g in gateway:
        gd = datetime.fromisoformat(g["captured_at"]).date()
        for b in bank_by_amount.get(g["amount"], []):
            bd = datetime.strptime(b["value_date"], "%d-%m-%Y").date()
            if 0 <= (bd - gd).days <= 3:
                resolved += 1
                break
    # ----------------------------------------------------------------------

    pct = 100.0 * resolved / len(gateway)
    print("CHECK 1 — naive exact-key matcher (amount + T+0..T+3 window)")
    print(f"  gateway payments      : {len(gateway)}")
    print(f"  naively resolved      : {resolved}")
    print(f"  resolve rate          : {pct:.2f}%")
    print(f"  PLAN gate (<= ~70%)   : {'PASS' if pct <= 70 else 'FAIL — batch too easy'}")

    # ---------- CHECK 2: is the truth still recoverable at all? ----------
    bank_utrs = {b["utr"] for b in bank if b["utr"]}
    paid = [s for s in truth["settlements"] if s["paid"]]
    found = sum(1 for s in paid if s["utr"] in bank_utrs)
    pct2 = 100.0 * found / len(paid) if paid else 0.0
    print()
    print("CHECK 2 — solvability: are true settlements recoverable by reference?")
    print(f"  settlements truly paid: {len(paid)}")
    print(f"  UTR present in bank   : {found}")
    print(f"  recoverable via UTR   : {pct2:.2f}%")
    print(f"  (remainder must be resolved by narration or subset-sum, by design)")

    print()
    print("WHY THE NAIVE MATCHER FAILS — structural, not injected noise:")
    print("  A bank credit is a settlement NET of fee, GST, refunds, chargebacks")
    print("  and reserve across many payments. No bank row equals a payment amount")
    print("  unless by coincidence. The bank cannot see a payment; it only ever")
    print("  sees money arriving. That is why N:1 subset-sum has to exist.")
    return 0 if pct <= 70 else 1


if __name__ == "__main__":
    raise SystemExit(main())
