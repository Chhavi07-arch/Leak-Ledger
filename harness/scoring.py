"""Per-class scoring, shared by the scorecard and the report.

One implementation so the two surfaces cannot disagree about precision, which is
exactly the sort of drift that lets a misleading number survive in one place
after being corrected in the other.

THE VALUE QUESTION, DECIDED EXPLICITLY (INC-017)

Should a class's reported rupee value sum ALL found instances, or only those the
harness can verify as true positives?

**All found instances** -- because that is what the tool actually claims when it
runs. A deployed engine has no ground truth; it cannot filter its own output down
to the ones that happen to be right. Reporting only verified value would flatter
the tool by using knowledge it does not possess at run time, and would make the
headline unreproducible outside this repository.

But reporting the gross figure without disclosing precision is how a headline
becomes confidently wrong. So both are computed and both are shown:

  found_value     what the engine claims (all instances)
  verified_value  the subset ground truth confirms
  unverified_value the remainder -- money the engine flagged that is NOT missing

and the headline separates CONFIRMED classes (precision 1.00) from FLAGGED
classes (precision below 1.00), so no reader has to infer the difference.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from leakledger.money import Money                                            # noqa: E402


@dataclass
class ClassScore:
    leak_class: str
    found: int
    seeded: int
    tp: int
    fp: int
    fn: int
    found_value: Money
    verified_value: Money
    unverified_value: Money
    scoreable: bool = True          # False where ground truth cannot adjudicate

    @property
    def precision(self) -> Optional[float]:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else None

    @property
    def recall(self) -> Optional[float]:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else None

    @property
    def confirmed(self) -> bool:
        """A class whose every reported instance is a true positive."""
        return self.scoreable and self.fp == 0 and self.tp > 0


def build_entity_map(truth, bank_rows) -> Dict[str, str]:
    """finding-space id -> ground-truth-space id.

    DUPLICATE_PAYOUT names a BANK TRANSACTION because that is what a human opens
    and acts on; ground truth names the SETTLEMENT that was paid twice. The
    scorer carries the mapping rather than relabelling findings, because
    relabelling would damage the product to please the scorer.
    """
    by_net = defaultdict(list)
    for s in truth["settlements"]:
        by_net[s["net_paise"]].append(s["settlement_id"])
    out = {}
    for b in bank_rows:
        if b["direction"] != "CR":
            continue
        c = by_net.get(Money.from_rupees_str(b["amount"]).paise, [])
        if len(c) == 1:
            out[b["txn_id"]] = c[0]
    return out


def score_all(found, truth, bank_rows) -> Dict[str, ClassScore]:
    seeded: Dict[str, set] = defaultdict(set)
    for l in truth["seeded_leaks"]:
        seeded[l["class"]].add(l["entity_id"])
    entity_map = build_entity_map(truth, bank_rows)
    unpaid = {p for s in truth["settlements"] if not s["paid"] for p in s["payment_ids"]}
    n_unpaid_cycles = sum(1 for s in truth["settlements"] if not s["paid"])

    out: Dict[str, ClassScore] = {}
    for cls, fl in found.by_class().items():
        if cls == "MISSING_SETTLEMENT":
            tps = [f for f in fl if set(f.evidence.get("payment_ids", [])) & unpaid]
            fps = [f for f in fl if f not in tps]
            tp, fp = len(tps), len(fps)
            fn = max(0, n_unpaid_cycles - tp)
        elif cls == "SHORT_SETTLEMENT":
            # Detectable but not quantifiable; ground truth cannot adjudicate an
            # individual flag, so it is reported as unscoreable rather than scored
            # against a comparison it does not support (INC-013).
            tps, fps = [], list(fl)
            tp, fp, fn = 0, 0, len(seeded.get(cls, set()))
            out[cls] = ClassScore(cls, len(fl), len(seeded.get(cls, set())), tp, fp, fn,
                                  Money.sum(f.value for f in fl), Money.zero(),
                                  Money.zero(), scoreable=False)
            continue
        else:
            s = seeded.get(cls, set())
            ids = {entity_map.get(f.entity_id, f.entity_id) for f in fl} \
                if cls == "DUPLICATE_PAYOUT" else {f.entity_id for f in fl}
            tps = [f for f in fl
                   if (entity_map.get(f.entity_id, f.entity_id)
                       if cls == "DUPLICATE_PAYOUT" else f.entity_id) in s]
            fps = [f for f in fl if f not in tps]
            tp, fp, fn = len(ids & s), len(ids - s), len(s - ids)
        out[cls] = ClassScore(
            cls, len(fl), len(seeded.get(cls, set())), tp, fp, fn,
            Money.sum(f.value for f in fl),
            Money.sum(f.value for f in tps) if tps else Money.zero(),
            Money.sum(f.value for f in fps) if fps else Money.zero())
    return out


def headline_split(found, scores: Dict[str, ClassScore]):
    """(confirmed_value, flagged_value, flagged_classes).

    CONFIRMED = every reported instance is a true positive.
    FLAGGED   = the class reports instances ground truth does not confirm.
    Exception signals carry no value and appear in neither.
    """
    from leakledger.leakage.findings import EXCEPTION_SIGNAL
    confirmed, flagged, flagged_classes = Money.zero(), Money.zero(), []
    for cls, fl in found.by_class().items():
        if fl[0].category == EXCEPTION_SIGNAL:
            continue
        sc = scores.get(cls)
        if sc and sc.confirmed:
            confirmed = confirmed + sc.found_value
        else:
            flagged = flagged + (sc.found_value if sc else Money.zero())
            if sc:
                flagged_classes.append(sc)
    return confirmed, flagged, flagged_classes
