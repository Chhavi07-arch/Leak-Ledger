"""Deviation search for settlement reconciliation, with uniqueness enforcement.

WHY THIS IS NOT A CLASSIC SUBSET-SUM.

PLAN.md framed T3 as "find a subset of payments summing to this payout, bounded
at k <= 12". Measured against the real batch that framing is wrong: settlements
carry a median of 24 payments and up to 42, so the true answer is almost always
the *whole* candidate pool. A size-bounded subset search could never find it.

The real question: the engine infers a settlement's cycle from the bank value
date, and pools payments by capture DATE. It is not told the acquirer's cutoff
TIME, so evening payments may have rolled into a neighbouring payout. It
therefore starts from the presumed pool and searches for a small DEVIATION --
payments to exclude, or to include from an adjacent day.

ALGORITHM (v2, meet-in-the-middle). See INC-008.

The first implementation enumerated combinations directly, which is O(C(n,d))
and became the binding constraint: at bound 4 the full batch took 13.9s, at
bound 5 it took 33.9s, and once candidate-cycle widening (INC-007) multiplied the
work it exceeded two minutes. The bound was therefore being chosen by what the
CPU tolerated rather than by what the data required -- and that silently
suppressed correctness, because the settlements holding ambiguity traps need
d=4 and d=5.

The reduction that fixes it: deductions are per-payment and additive, so a
payment's contribution to the payout is a single integer

    value_i = amount_i - fee_i - gst_i

and reconciliation becomes, with S = (pool \\ excluded) U included,

    sum(value_i for i in S) == target + cycle_level_deductions

Substituting S and letting delta = target' - sum(pool values):

    sum(included) - sum(excluded) == delta

a signed, size-bounded subset-sum. Neighbours are few (a handful of late-evening
payments on adjacent days), so all their subsets are enumerated directly. For
each, the pool side is solved by meet-in-the-middle: split the pool in half,
enumerate size-bounded subset sums of each half into a hash index, and join.
That turns C(42,5) = 850,668 into roughly 2 x C(21,<=5) = 55,792 -- and the join
yields exact SOLUTION COUNTS, which is what ambiguity detection needs.

UNIQUENESS IS THE POINT. Every solution at the minimum deviation size is
enumerated, not the first one found. If two or more distinct deviations reconcile
the same payout, the engine refuses and raises AMBIGUOUS_SUBSET. Returning the
first hit is the single most common way to manufacture a silent false match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

from ..money import Money

DEFAULT_DEVIATION_BOUND = 6
DEFAULT_COMBINATION_BUDGET = 5_000_000
MAX_NEIGHBOUR_ENUMERATION = 20      # 2**20 guard; real neighbour sets are tiny


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


def _half_index(items: Sequence[Tuple[str, int]], max_k: int, budget_counter: List[int]):
    """Map {size: {sum: [subsets]}} for all subsets of `items` up to size max_k.

    Deterministic: `items` arrives sorted by id, and combinations() preserves
    that order, so identical inputs always produce identical subset orderings.
    """
    index: Dict[int, Dict[int, List[Tuple[str, ...]]]] = {}
    ids = [i for i, _ in items]
    vals = {i: v for i, v in items}
    for k in range(0, max_k + 1):
        bucket: Dict[int, List[Tuple[str, ...]]] = {}
        for combo in combinations(ids, k):
            budget_counter[0] += 1
            s = sum(vals[i] for i in combo)
            bucket.setdefault(s, []).append(combo)
        index[k] = bucket
    return index


def search_deviation(
    *,
    target_net: Money,
    pool: Dict[str, Money],
    neighbours: Dict[str, Money],
    deduction_for: Callable,
    tolerance: Money = Money(0),
    bound: int = DEFAULT_DEVIATION_BOUND,
    budget: int = DEFAULT_COMBINATION_BUDGET,
    value_of: Optional[Callable] = None,
) -> SearchResult:
    """Find every deviation of size <= bound that reconciles the pool to target.

    `deduction_for(ids)` returns fee + GST + cycle-level deductions for a set of
    payments, so the caller owns the contract arithmetic and this module owns only
    the search. `value_of(id)` optionally supplies a payment's precomputed net
    contribution; without it the value is derived by differencing deduction_for,
    which keeps this module agnostic about how deductions are structured.

    Tolerance is supported but exact (0) is the default: money is integer paise,
    so an exact match is the honest test and a tolerance band would let a genuine
    short settlement pass as reconciled.
    """
    pool_ids = sorted(pool)
    neigh_ids = sorted(neighbours)
    if len(neigh_ids) > MAX_NEIGHBOUR_ENUMERATION:
        return SearchResult(status="BUDGET_EXCEEDED", bound_used=bound,
                            detail=f"{len(neigh_ids)} neighbours exceeds enumeration guard")

    # --- reduce to signed subset-sum over integer net contributions ---
    base_ded = deduction_for(pool_ids)
    if value_of is None:
        def value_of(pid, _cache={}):                                   # noqa: B006
            if pid not in _cache:
                one = deduction_for([pid]) - deduction_for([])
                src = pool.get(pid) or neighbours.get(pid)
                _cache[pid] = src.paise - one.paise
            return _cache[pid]

    pool_vals = [(i, value_of(i)) for i in pool_ids]
    neigh_vals = [(i, value_of(i)) for i in neigh_ids]
    # cycle-level deductions = total deduction minus the per-payment part
    per_payment = sum((pool[i].paise - value_of(i)) for i in pool_ids)
    cycle_level = base_ded.paise - per_payment

    pool_sum = sum(v for _, v in pool_vals)
    delta = (target_net.paise + cycle_level) - pool_sum

    counter = [0]
    mid = len(pool_vals) // 2
    left = _half_index(pool_vals[:mid], bound, counter)
    right = _half_index(pool_vals[mid:], bound, counter)
    if counter[0] > budget:
        return SearchResult(status="BUDGET_EXCEEDED", combinations_examined=counter[0],
                            bound_used=bound,
                            detail=f"pool indexing exceeded {budget:,} combinations")

    solutions_by_size: Dict[int, List[Deviation]] = {}

    for n_in in range(0, min(bound, len(neigh_ids)) + 1):
        for inc in combinations(neigh_ids, n_in):
            counter[0] += 1
            inc_sum = sum(value_of(i) for i in inc)
            need_excluded_sum = inc_sum - delta      # sum(excluded) must equal this
            max_ex = bound - n_in
            for kl in range(0, max_ex + 1):
                for kr in range(0, max_ex - kl + 1):
                    for ls, lsubs in left[kl].items():
                        rs = need_excluded_sum - ls
                        rsubs = right[kr].get(rs)
                        if not rsubs:
                            continue
                        size = n_in + kl + kr
                        for a in lsubs:
                            for b in rsubs:
                                solutions_by_size.setdefault(size, []).append(
                                    Deviation(frozenset(a + b), frozenset(inc))
                                )
        if solutions_by_size and min(solutions_by_size) <= n_in:
            break        # no larger inclusion set can beat an already-smaller solution

    if not solutions_by_size:
        return SearchResult(status="NO_SOLUTION", combinations_examined=counter[0],
                            bound_used=bound,
                            detail=f"no deviation of size <= {bound} reconciles this payout")

    best = min(solutions_by_size)
    sols = solutions_by_size[best]
    # de-duplicate: a deviation is identified by its (excluded, included) pair
    seen, uniq = set(), []
    for s in sols:
        key = (s.excluded, s.included)
        if key not in seen:
            seen.add(key)
            uniq.append(s)
    uniq.sort(key=lambda d: (sorted(d.excluded), sorted(d.included)))

    if len(uniq) > 1:
        return SearchResult(status="AMBIGUOUS", solutions=uniq,
                            combinations_examined=counter[0], bound_used=bound,
                            detail=f"{len(uniq)} distinct deviations of size {best} reconcile "
                                   f"identically; refusing to choose")
    return SearchResult(status="SOLVED", solutions=uniq, combinations_examined=counter[0],
                        bound_used=bound, detail=uniq[0].describe())
