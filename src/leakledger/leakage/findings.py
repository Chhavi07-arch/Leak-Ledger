"""A leakage finding, and the arithmetic that produced it.

Every finding carries its own derivation. A finding a human cannot re-derive in
ten seconds is a finding they will not act on, and a reviewer knows that.

Findings are split into three categories, and the split is a disclosure rather
than a taxonomy convenience:

  STRUCTURAL         detects money loss WITHOUT needing to know a contract rate.
                     Sound half of the taxonomy; leads the headline.
  CONTRACT_DEPENDENT compares against a fee schedule authored in this repository,
                     from which the data was also generated. Verification, not
                     discovery -- see PLAN.md and ADR-001.
  RULE_CHECK         precision 1.0 by construction. Reported, but EXCLUDED from
                     aggregate precision/recall, because including it inflates the
                     scorecard with a free win.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..money import Money

STRUCTURAL = "STRUCTURAL"
CONTRACT_DEPENDENT = "CONTRACT_DEPENDENT"
RULE_CHECK = "RULE_CHECK"
# Not a leak class. A typed signal that a cycle would not reconcile, carrying no
# rupee value because none can be honestly derived (INC-013). Excluded from the
# headline total and from aggregate precision/recall; it belongs in the exception
# queue, which is where "this payout does not add up and I cannot tell you by how
# much" is the correct thing to say.
EXCEPTION_SIGNAL = "EXCEPTION_SIGNAL"

CATEGORY = {
    "MISSING_SETTLEMENT": STRUCTURAL,
    "DUPLICATE_PAYOUT": STRUCTURAL,
    "DUPLICATE_CAPTURE": STRUCTURAL,
    "REFUND_NOT_REACHED": STRUCTURAL,
    "CHARGEBACK_NOT_RECREDITED": STRUCTURAL,
    "RESERVE_NOT_RELEASED": STRUCTURAL,
    "FEE_OVERCHARGE": CONTRACT_DEPENDENT,
    "GST_MISMATCH": CONTRACT_DEPENDENT,
    "SHORT_SETTLEMENT": EXCEPTION_SIGNAL,
    "ZERO_MDR_VIOLATION": RULE_CHECK,
}


@dataclass
class Finding:
    finding_id: str
    leak_class: str
    entity_id: str
    value: Money
    derivation: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def category(self) -> str:
        return CATEGORY[self.leak_class]

    def line(self) -> str:
        return (f"[{self.leak_class:26}] {self.entity_id:16} "
                f"Rs {self.value.to_rupees_str():>12}  {self.derivation}")


@dataclass
class FindingSet:
    findings: List[Finding] = field(default_factory=list)

    def add(self, leak_class, entity_id, value: Money, derivation: str, **evidence) -> Finding:
        f = Finding(f"find_{len(self.findings):04d}", leak_class, entity_id,
                    value, derivation, evidence)
        self.findings.append(f)
        return f

    def by_class(self) -> Dict[str, List[Finding]]:
        out: Dict[str, List[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.leak_class, []).append(f)
        return out

    def by_category(self, category: str) -> List[Finding]:
        return [f for f in self.findings if f.category == category]

    def total(self, category: str = None) -> Money:
        """Headline total excludes EXCEPTION_SIGNAL findings, which carry no value."""
        if category is None:
            src = [f for f in self.findings if f.category != EXCEPTION_SIGNAL]
        else:
            src = self.by_category(category)
        return Money.sum(f.value for f in src)
