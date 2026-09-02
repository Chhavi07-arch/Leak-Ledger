"""Double-entry ledger with idempotent apply.

Two invariants, both asserted in CI rather than intended:

  DOUBLE ENTRY   every posting sums to zero across accounts. A reconciliation
                 tool that can create money is not a reconciliation tool.
  IDEMPOTENCE    applying the same run twice changes nothing. This is what makes
                 an auto-applied match reversible, which is the actual reason a
                 human is allowed to skip reviewing one (PLAN.md, "Why an
                 auto-applied match can go unreviewed") -- not that the match is
                 certainly right, but that being wrong is detectable and
                 recoverable.

Idempotence is keyed on (run_id, entry_key), where entry_key identifies the
business fact being recorded, not the attempt. Re-running a batch, or re-running
after a crash midway, converges to the same ledger.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .clock import IST
from .money import Money

# accounts
BANK = "BANK"
MERCHANT_RECEIVABLE = "MERCHANT_RECEIVABLE"
GATEWAY_FEE = "GATEWAY_FEE_EXPENSE"
GST_INPUT = "GST_INPUT_CREDIT"
LEAKAGE_SUSPENSE = "LEAKAGE_SUSPENSE"
UNRECONCILED = "UNRECONCILED_SUSPENSE"

ACCOUNTS = (BANK, MERCHANT_RECEIVABLE, GATEWAY_FEE, GST_INPUT, LEAKAGE_SUSPENSE, UNRECONCILED)


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class Posting:
    account: str
    amount: Money            # signed; debits positive, credits negative

    def __post_init__(self):
        if self.account not in ACCOUNTS:
            raise LedgerError(f"unknown account {self.account!r}")


@dataclass
class Entry:
    entry_key: str           # identifies the FACT, not the attempt
    run_id: str
    ts: str
    narrative: str
    postings: List[Posting]

    def check_balanced(self) -> None:
        total = Money.sum(p.amount for p in self.postings)
        if total.paise != 0:
            raise LedgerError(
                f"entry {self.entry_key} does not balance: {total} "
                f"({[(p.account, str(p.amount)) for p in self.postings]})")


class Ledger:
    def __init__(self):
        self._entries: Dict[str, Entry] = {}      # entry_key -> Entry
        self._applied_keys: set = set()

    # ---- posting ------------------------------------------------------
    def post(self, *, entry_key: str, run_id: str, narrative: str,
             postings: Iterable[Tuple[str, Money]]) -> bool:
        """Record a fact. Returns True if newly posted, False if already present.

        Idempotent by entry_key: a repeated fact is a no-op, not a duplicate.
        """
        if entry_key in self._entries:
            return False
        entry = Entry(entry_key=entry_key, run_id=run_id,
                      ts=datetime.now(IST).isoformat(), narrative=narrative,
                      postings=[Posting(a, m) for a, m in postings])
        entry.check_balanced()
        self._entries[entry_key] = entry
        return True

    # ---- inspection ---------------------------------------------------
    def balances(self) -> Dict[str, Money]:
        out = {a: Money.zero() for a in ACCOUNTS}
        for e in self._entries.values():
            for p in e.postings:
                out[p.account] = out[p.account] + p.amount
        return out

    def trial_balance(self) -> Money:
        return Money.sum(self.balances().values())

    def __len__(self) -> int:
        return len(self._entries)

    def state_hash(self) -> str:
        """Content hash of ledger state, independent of insertion order or time."""
        rows = sorted(
            (e.entry_key, tuple(sorted((p.account, p.amount.paise) for p in e.postings)))
            for e in self._entries.values())
        return hashlib.sha256(
            json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def apply_run(ledger: Ledger, *, run_id: str, cascade_result, findings,
              payments) -> Dict[str, int]:
    """Post a completed run. Safe to call repeatedly with the same inputs.

    Only AUTO_APPLY matches reach the ledger. Review-queue items and exceptions
    do not: an unresolved payout is not a fact, and posting it would be the
    ledger equivalent of INC-010 -- recording ignorance as an outcome.
    """
    pay = {p.payment_id: p for p in payments}
    stats = {"posted": 0, "skipped_existing": 0, "not_eligible": 0}

    for m in cascade_result.matches:
        if m.disposition != "AUTO_APPLY":
            stats["not_eligible"] += 1
            continue
        gross = Money.sum(pay[i].amount for i in m.matched_ids if i in pay)
        fee = Money.sum(pay[i].fee_charged for i in m.matched_ids
                        if i in pay and pay[i].fee_charged)
        gst = Money.sum(pay[i].gst_charged for i in m.matched_ids
                        if i in pay and pay[i].gst_charged)
        if gross.paise == 0:
            stats["not_eligible"] += 1
            continue
        newly = ledger.post(
            entry_key=f"match:{m.bank_txn_id}",
            run_id=run_id,
            narrative=f"{m.tier} match of {m.bank_txn_id} to {len(m.matched_ids)} records",
            postings=[(BANK, gross - fee - gst), (GATEWAY_FEE, fee), (GST_INPUT, gst),
                      (MERCHANT_RECEIVABLE, -gross)])
        stats["posted" if newly else "skipped_existing"] += 1

    for f in findings.findings:
        if f.value.paise == 0:
            continue                     # EXCEPTION_SIGNAL findings carry no value
        newly = ledger.post(
            entry_key=f"finding:{f.leak_class}:{f.entity_id}",
            run_id=run_id,
            narrative=f"{f.leak_class} on {f.entity_id}",
            postings=[(LEAKAGE_SUSPENSE, f.value), (MERCHANT_RECEIVABLE, -f.value)])
        stats["posted" if newly else "skipped_existing"] += 1

    return stats
