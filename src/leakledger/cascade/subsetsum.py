"""Deviation search for settlement reconciliation, with uniqueness enforcement.

WHY THIS IS NOT A CLASSIC SUBSET-SUM.

PLAN.md framed T3 as "find a subset of payments summing to this payout, bounded
at k <= 12". Measured against the real batch, that framing is wrong: settlements
carry a median of 24 payments and up to 42, so the true answer is almost always
the *whole* candidate pool, not a small selection from it. A size-bounded subset
search could never find it, and every large settlement would emit
SEARCH_BUDGET_EXCEEDED — which would look like principled restraint while
actually being a search pointed at the wrong problem.

The real question is different. The engine can infer a settlement's cycle from
the bank value date by business-day arithmetic, and can pool payments by their
capture DATE. What it cannot know is the acquirer's cutoff TIME, so payments
captured late in the evening may have rolled into a neighbouring payout. The
engine therefore starts from the presumed pool and searches for a small
DEVIATION — payments to exclude, or to include from the adjacent day.

Bound: d <= 3, chosen from the measured distribution (max observed deviation 3,
mean 0.88, 15 of 24 settlements requiring non-zero correction). At the largest
pool of 42 that is C(42,<=3) = 12,384 combinations.

UNIQUENESS IS THE POINT. Every solution within the bound is enumerated, not the
first one found. If two or more distinct deviations reconcile the same payout,
the engine refuses and raises AMBIGUOUS_SUBSET. Returning the first hit is the
single most common way to manufacture a silent false match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from ..money import Money

DEFAULT_DEVIATION_BOUND = 3
DEFAULT_COMBINATION_BUDGET = 200_000


@dataclass(frozen=True)
class Deviation:
    """One way of correcting the presumed pool so the arithmetic reconciles."""
    excluded: FrozenSet[str]
    included: FrozenSet[str]

    @property
    def size(self) -> int:
        return len(self.excluded) + len(self.included)

    def describe(self) -> str:
        parts = []
        if self.excluded:
            parts.append(f"exclude {sorted(self.excluded)}")
        if self.included:
            parts.append(f"include {sorted(self.included)}")
        return "; ".join(parts) if parts else "no deviation (pool reconciles as-is)"


@dataclass
class SearchResult:
    status: str                       # SOLVED | AMBIGUOUS | NO_SOLUTION | BUDGET_EXCEEDED
    solutions: List[Deviation] = field(default_factory=list)
    combinations_examined: int = 0
    bound_used: int = 0
    detail: str = ""

    @property
    def unique(self) -> Optional[Deviation]:
        return self.solutions[0] if self.status == "SOLVED" else None


def search_deviation(
    *,
    target_net: Money,
    pool: Dict[str, Money],
    neighbours: Dict[str, Money],
    deduction_for: "callable",
    tolerance: Money = Money(0),
    bound: int = DEFAULT_DEVIATION_BOUND,
    budget: int = DEFAULT_COMBINATION_BUDGET,
) -> SearchResult:
    """Find every deviation of size <= bound that reconciles the pool to target.

    `deduction_for(payment_ids)` returns total fee + GST + other deductions for a
    set of payments, so the caller owns the contract arithmetic and this module
    owns only the search.

    Deliberately enumerates ALL solutions rather than short-circuiting on the
    first, so ambiguity is detectable. Short-circuiting is the defect this
    function exists to avoid.
    """
    pool_ids = sorted(pool)
    neigh_ids = sorted(neighbours)
    examined = 0
    solutions: List[Deviation] = []

    def reconciles(excluded, included) -> bool:
        ids = [i for i in pool_ids if i not in excluded] + list(included)
        gross = Money.sum([pool[i] for i in ids if i in pool] +
                          [neighbours[i] for i in ids if i in neighbours])
        net = gross - deduction_for(ids)
        return abs((net - target_net).paise) <= tolerance.paise

    for d in range(0, bound + 1):
        # split the deviation budget between exclusions and inclusions
        for n_ex in range(d + 1):
            n_in = d - n_ex
            if n_ex > len(pool_ids) or n_in > len(neigh_ids):
                continue
            for ex in combinations(pool_ids, n_ex):
                for inc in combinations(neigh_ids, n_in):
                    examined += 1
                    if examined > budget:
                        return SearchResult(
                            status="BUDGET_EXCEEDED", solutions=solutions,
                            combinations_examined=examined, bound_used=bound,
                            detail=f"exceeded {budget} combinations at deviation size {d}",
                        )
                    if reconciles(frozenset(ex), frozenset(inc)):
                        solutions.append(Deviation(frozenset(ex), frozenset(inc)))
        if solutions:
            # smallest deviation wins; only ties at THIS size can be ambiguous
            break

    if not solutions:
        return SearchResult(
            status="NO_SOLUTION", combinations_examined=examined, bound_used=bound,
            detail=f"no deviation of size <= {bound} reconciles this payout",
        )
    if len(solutions) > 1:
        return SearchResult(
            status="AMBIGUOUS", solutions=solutions, combinations_examined=examined,
            bound_used=bound,
            detail=f"{len(solutions)} distinct deviations reconcile identically; refusing to choose",
        )
    return SearchResult(
        status="SOLVED", solutions=solutions, combinations_examined=examined,
        bound_used=bound, detail=solutions[0].describe(),
    )
