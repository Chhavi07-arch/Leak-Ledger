"""The cascade: T0 canonicalise, T1 exact reference, T2 unique amount+window,
T3 settlement deviation search, T5 typed exception.

Tiers are strictly ordered and a record matched at tier n never falls through.
Tier labels are ORDINAL RANKS ASSIGNED BY RULE, not calibrated probabilities —
see PLAN.md, "Why an auto-applied match can go unreviewed". Nothing in this build
establishes that a T2 match is correct 90% of the time, and the label must never
be presented as though it did.

What the engine is allowed to know is deliberately constrained. It infers a
settlement cycle from the bank value date by business-day arithmetic, and pools
payments by capture DATE. It is never told the acquirer's cutoff TIME, so late
-evening payments that rolled into a neighbouring payout must be discovered by
search rather than looked up. Handing it `cycle_date_for_capture()` — the same
function the generator used — would make the pool correct by construction and
the search a no-op that reported a perfect score while proving nothing.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence

from ..clock import IST, BusinessCalendar, SETTLEMENT_LAG_BUSINESS_DAYS
from ..money import Money
from .subsetsum import DEFAULT_DEVIATION_BOUND, search_deviation

# Bound chosen from the measured TRUE deviation distribution over paid
# settlements -- {0:1, 1:8, 2:9, 3:2, 4:1, 5:2, 6:1} -- so d<=6 covers every real
# case. It is also the largest bound before the search begins manufacturing
# SPURIOUS ambiguity: at d<=7 the refusal count rises from 5 to 9 as coincidental
# alternative reconciliations appear, which would refuse matches that should be
# made. Wider is not strictly better. See ADR-004.

# --- dispositions -------------------------------------------------------
AUTO_APPLY = "AUTO_APPLY"
REVIEW = "REVIEW"
EXCEPTION = "EXCEPTION"

# --- typed exception reason codes (no untyped exits) --------------------
AMBIGUOUS_SUBSET = "AMBIGUOUS_SUBSET"
AMBIGUOUS_CANDIDATE = "AMBIGUOUS_CANDIDATE"
SEARCH_BUDGET_EXCEEDED = "SEARCH_BUDGET_EXCEEDED"
NO_RECONCILING_SET = "NO_RECONCILING_SET"
# Distinct from the above: a set may well exist, but not within the deviation
# bound this engine agreed to search. That is a principled refusal, not a
# failure to find, and conflating the two overstates what was actually ruled out.
DEVIATION_BOUND_EXCEEDED = "DEVIATION_BOUND_EXCEEDED"
# The credit's own T+2 cycle has payments but will not reconcile. Searching
# further-out cycles from here is how a genuinely short payout gets matched to a
# disjoint set of payments four days away (INC-012). The honest reading is that
# something is wrong with THIS cycle, so the engine stops and says so.
PRIMARY_CYCLE_UNRECONCILED = "PRIMARY_CYCLE_UNRECONCILED"
REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"
UNEXPLAINED_RESIDUAL = "UNEXPLAINED_RESIDUAL"


@dataclass
class Match:
    bank_txn_id: str
    tier: str
    disposition: str
    matched_ids: List[str] = field(default_factory=list)
    reason_code: Optional[str] = None
    evidence: str = ""
    combinations_examined: int = 0
    # nearest-residual context, populated only when no exact reconciliation
    # exists. A CANDIDATE, never a conclusion -- see detect_short_settlement.
    residual_paise: Optional[int] = None
    residual_unique: bool = False
    residual_deviation_size: Optional[int] = None
    pool_gross_paise: Optional[int] = None
    # The competing reconciliations behind an AMBIGUOUS_SUBSET refusal. Carried
    # so a reader can SEE the answers the engine declined to choose between,
    # rather than being told how many there were. A refusal a reviewer cannot
    # inspect is indistinguishable from a failure to try.
    competing: List[dict] = field(default_factory=list)


@dataclass
class CascadeResult:
    matches: List[Match] = field(default_factory=list)

    def by_disposition(self, d: str) -> List[Match]:
        return [m for m in self.matches if m.disposition == d]

    def by_tier(self) -> Dict[str, int]:
        out: Dict[str, int] = defaultdict(int)
        for m in self.matches:
            out[m.tier] += 1
        return dict(out)

    def by_reason(self) -> Dict[str, int]:
        out: Dict[str, int] = defaultdict(int)
        for m in self.matches:
            if m.reason_code:
                out[m.reason_code] += 1
        return dict(out)


class Cascade:
    def __init__(self, *, payments, refunds, bank, calendar: BusinessCalendar,
                 adjustments=None, model_provider=None,
                 bound: int = DEFAULT_DEVIATION_BOUND, tolerance: Money = Money(0)):
        self.payments = {p.payment_id: p for p in payments}
        self.refunds = refunds
        self.adjustments = adjustments or []
        # T4 only. None disables the tier entirely -- the deterministic tiers do
        # not consult it and cannot be influenced by it.
        self.model_provider = model_provider
        self.bank = bank
        self.calendar = calendar
        self.bound = bound
        self.tolerance = tolerance
        self._by_capture_date: Dict[date, List[str]] = defaultdict(list)
        for p in payments:
            self._by_capture_date[p.captured_at.date()].append(p.payment_id)

    def _netted_refunds_for_cycle(self, cycle: date) -> Money:
        """Refunds that net against this payout rather than debiting separately.

        The engine HAS this data — it is in the gateway refunds export — and must
        use it. Omitting it was INC-003: the arithmetic could never close on any
        cycle containing a netted refund, and 11 of 24 settlements failed for a
        reason that had nothing to do with the search.
        """
        total = Money.zero()
        for r in self.refunds:
            if r.get("mode") != "NETTED":
                continue
            issued = datetime.fromisoformat(r["issued_at"]).date()
            if issued == cycle:
                total = total + Money.from_rupees_str(r["amount"])
        return total

    def _adjustments_for_cycle(self, cycle: date) -> Money:
        """Chargeback debits/credits and reserve holds posted in this cycle."""
        total = Money.zero()
        for a in self.adjustments:
            if date.fromisoformat(a["posted_date"]) != cycle:
                continue
            amt = Money.from_rupees_str(a["amount"])
            if a["kind"] in ("CHARGEBACK_DEBIT", "RESERVE_HELD"):
                total = total + amt
            elif a["kind"] in ("CHARGEBACK_CREDIT", "RESERVE_RELEASED"):
                total = total - amt
        return total

    # ---- deduction arithmetic (observed, not contractual) -------------
    def _deduction_for(self, ids: Sequence[str]) -> Money:
        """Fee + GST as the gateway REPORTED them.

        Observed values, deliberately — matching must reconcile against what was
        actually charged. Comparing against the contract is a separate question,
        answered in Phase 04, and conflating the two would turn every overcharge
        into an unmatched settlement instead of a finding.
        """
        total = Money.zero()
        for i in ids:
            p = self.payments.get(i)
            if p is None:
                continue
            if p.fee_charged:
                total = total + p.fee_charged
            if p.gst_charged:
                total = total + p.gst_charged
        return total

    # A bank may post a credit a day or two after the acquirer released it. The
    # engine is not told when that happened, so every candidate cycle is computed
    # for a WINDOW of plausible release dates, not for the observed date alone.
    # Without this, a single day of posting lag moves the inferred cycle wholesale
    # and the true pool becomes unreachable at any deviation bound (INC-007).
    POSTING_LAG_TOLERANCE_DAYS = 2

    def _candidate_cycles(self, value_date: date, lookback: int = 8) -> List[date]:
        """Every cycle date that T+2 business-day dating could map to this value date.

        The forward map is NOT injective and therefore cannot be inverted to a
        single date. add_business_days(Fri, 2) and add_business_days(Sat, 2) both
        land on Tuesday, because the walk skips the weekend either way. A single
        backward walk lands on Friday and silently misses every Saturday cycle —
        which is what INC-005 was: 4 of 9 T3 failures were settlements whose cycle
        date fell on a non-business day.

        Returning the full candidate set is the honest inversion. The engine does
        not know which cycle produced the payout, so it tries each and lets the
        arithmetic decide. If more than one reconciles, that is genuine ambiguity
        and is refused, not resolved by preferring the earlier date.
        """
        out = set()
        for k in range(self.POSTING_LAG_TOLERANCE_DAYS + 1):
            rd = value_date - timedelta(days=k)
            for back in range(1, lookback + 1):
                d = rd - timedelta(days=back)
                if self.calendar.add_business_days(d, SETTLEMENT_LAG_BUSINESS_DAYS) == rd:
                    out.add(d)
        return sorted(out)

    def _candidate_cycles_by_lag(self, value_date: date, lookback: int = 8):
        """Candidate cycles grouped by assumed posting lag, nearest lag first.

        PROXIMITY IS EVIDENCE (INC-012). Treating a 4-day-lagged candidate as
        equally plausible as a zero-lag one produced a false match: a settlement
        that was genuinely short could not reconcile in its own cycle, so the
        search found a spurious exact reconciliation four days away and matched a
        completely disjoint set of payments. Candidates are now tried in lag
        order and the search stops at the first lag that yields any solution, so
        a distant cycle can only win when no nearer one reconciles at all.
        """
        groups = []
        seen = set()
        for k in range(self.POSTING_LAG_TOLERANCE_DAYS + 1):
            rd = value_date - timedelta(days=k)
            tier = []
            for back in range(1, lookback + 1):
                d = rd - timedelta(days=back)
                if (self.calendar.add_business_days(d, SETTLEMENT_LAG_BUSINESS_DAYS) == rd
                        and d not in seen):
                    seen.add(d)
                    tier.append(d)
            if tier:
                groups.append((k, sorted(tier)))
        return groups

    def _neighbours(self, cycle: date) -> Dict[str, Money]:
        """Late-evening payments on adjacent days: the only plausible strays.

        Restricting to >= 22:00 keeps the search space bounded without telling
        the engine where the cutoff actually is.
        """
        out: Dict[str, Money] = {}
        for delta in (-1, 1):
            for pid in self._by_capture_date.get(cycle + timedelta(days=delta), []):
                p = self.payments[pid]
                if p.captured_at.hour >= 22:
                    out[pid] = p.amount
        return out

    # ---- tiers --------------------------------------------------------
    def run(self) -> CascadeResult:
        result = CascadeResult()
        refunds_by_arn = {r.get("arn"): r for r in self.refunds if r.get("arn")}
        refunds_by_amount = defaultdict(list)
        for r in self.refunds:
            refunds_by_amount[r["amount"]].append(r)

        for b in self.bank:
            if b["direction"] == "DR":
                result.matches.append(self._match_debit(b, refunds_by_arn, refunds_by_amount))
            else:
                result.matches.append(self._match_credit(b))
        return result

    def _match_debit(self, b, refunds_by_arn, refunds_by_amount) -> Match:
        # --- T1: exact reference ---
        utr = (b.get("utr") or "").strip()
        if utr and utr in refunds_by_arn:
            r = refunds_by_arn[utr]
            if r["amount"] == b["amount"]:
                return Match(b["txn_id"], "T1", AUTO_APPLY, [r["refund_id"]],
                             evidence=f"ARN {utr} matches refund {r['refund_id']}, amounts agree")
            # exact key present but arithmetic disagrees — never trust the key alone
            return Match(b["txn_id"], "T1", EXCEPTION, [], UNEXPLAINED_RESIDUAL,
                         f"ARN {utr} matches refund {r['refund_id']} but amount "
                         f"{b['amount']} != {r['amount']}")
        # --- T2: unique amount within window ---
        cands = refunds_by_amount.get(b["amount"], [])
        bd = datetime.strptime(b["value_date"], "%d-%m-%Y").date()
        in_window = [r for r in cands
                     if abs((datetime.fromisoformat(r["issued_at"]).date() - bd).days) <= 2]
        if len(in_window) == 1:
            return Match(b["txn_id"], "T2", AUTO_APPLY, [in_window[0]["refund_id"]],
                         evidence=f"unique refund of {b['amount']} within +/-2d of {bd}")
        if len(in_window) > 1:
            return Match(b["txn_id"], "T2", EXCEPTION,
                         [r["refund_id"] for r in in_window], AMBIGUOUS_CANDIDATE,
                         f"{len(in_window)} refunds of {b['amount']} in window; refusing to choose")
        # --- T4: narration proposal, arithmetic-verified -----------------
        # The model may only ever CONFIRM a record that already exists at the
        # right amount. It cannot introduce a match, break a tie, or overturn a
        # refusal, and its confidence is not consulted. A rejected proposal
        # leaves the record exactly where it was.
        if self.model_provider is not None and b.get("narration"):
            from ..ai import narration as _narr
            by_ref = {}
            for r in self.refunds:
                arn = (r.get("arn") or "").strip()
                if arn:
                    by_ref[arn] = Money.from_rupees_str(r["amount"])
            cand = _narr.propose(self.model_provider, b["narration"])
            verdict = _narr.verify(cand, expected_amount=Money.from_rupees_str(b["amount"]),
                                   records_by_reference=by_ref)
            if verdict.accepted:
                rid = next((r["refund_id"] for r in self.refunds
                            if (r.get("arn") or "").strip() == verdict.matched_record_id), None)
                if rid:
                    return Match(b["txn_id"], "T4", REVIEW, [rid],
                                 evidence=f"narration proposal verified: {verdict.reason}")
            # proposal rejected -- fall through unchanged, never partially trusted

        return Match(b["txn_id"], "T5", EXCEPTION, [], REFERENCE_NOT_FOUND,
                     f"debit of {b['amount']} on {bd} matches no known refund")

    def _match_credit(self, b) -> Match:
        bd = datetime.strptime(b["value_date"], "%d-%m-%Y").date()
        lag_groups = self._candidate_cycles_by_lag(bd)
        candidates = [c for _, tier in lag_groups for c in tier]
        target = Money.from_rupees_str(b["amount"])
        examined = 0
        solved: List[tuple] = []      # (cycle, deviation, pool_ids)
        ambiguous_detail = None
        budget_hit = False
        bound_hit = False
        nearest_best = None
        ambiguous_solutions: List[dict] = []

        primary_had_pool = False
        for lag, tier in lag_groups:
          if solved or ambiguous_detail:
              break          # a nearer lag already explained this credit
          if lag > 0 and primary_had_pool:
              # Zero-lag cycle exists and has payments but did not reconcile.
              # Do NOT go looking further afield: the most likely explanation is
              # a defect in this cycle (a short payout, an unreported deduction),
              # not a four-day posting lag. Searching on is how INC-012 happened.
              break
          for cycle in tier:
            pool_ids = self._by_capture_date.get(cycle, [])
            if not pool_ids:
                continue
            if lag == 0:
                primary_had_pool = True
            pool = {i: self.payments[i].amount for i in pool_ids}
            neighbours = self._neighbours(cycle)
            netted = self._netted_refunds_for_cycle(cycle) + self._adjustments_for_cycle(cycle)

            def deduct(ids, _netted=netted):
                return self._deduction_for(ids) + _netted

            res = search_deviation(
                target_net=target, pool=pool, neighbours=neighbours,
                deduction_for=deduct, tolerance=self.tolerance, bound=self.bound,
            )
            examined += res.combinations_examined
            if res.status == "SOLVED":
                solved.append((cycle, res.solutions[0], pool_ids, netted, lag))
            elif res.status == "AMBIGUOUS":
                ambiguous_detail = f"cycle {cycle}: {res.detail}"
                ambiguous_solutions = [
                    {"cycle": str(cycle), "size": s.size,
                     "excluded": sorted(s.excluded), "included": sorted(s.included),
                     "describe": s.describe()}
                    for s in res.solutions]
            elif res.status == "BUDGET_EXCEEDED":
                budget_hit = True
            elif res.status == "NO_SOLUTION":
                bound_hit = True
                near = search_deviation(
                    target_net=target, pool=pool, neighbours=neighbours,
                    deduction_for=deduct, tolerance=self.tolerance,
                    bound=self.bound, nearest=True)
                if near.status == "NEAREST" and (
                        nearest_best is None or near.residual_paise < nearest_best[0]):
                    nearest_best = (near.residual_paise, near.nearest_unique,
                                    near.solutions[0].size if near.solutions else None,
                                    Money.sum(pool.values()).paise, cycle)

        # ambiguity within one cycle, OR across two candidate cycles, both refuse
        if ambiguous_detail:
            m = Match(b["txn_id"], "T3", EXCEPTION, [], AMBIGUOUS_SUBSET,
                      ambiguous_detail, examined)
            m.competing = ambiguous_solutions
            return m
        if len(solved) > 1:
            cycles = ", ".join(str(s[0]) for s in solved)
            m = Match(b["txn_id"], "T3", EXCEPTION, [], AMBIGUOUS_SUBSET,
                      f"{len(solved)} candidate cycles reconcile identically ({cycles}); "
                      f"refusing to choose", examined)
            m.competing = [
                {"cycle": str(c), "size": dev.size, "excluded": sorted(dev.excluded),
                 "included": sorted(dev.included), "describe": dev.describe()}
                for c, dev, _pool, _net, _lag in solved]
            return m
        if len(solved) == 1:
            cycle, dev, pool_ids, netted, lag = solved[0]
            ids = [i for i in pool_ids if i not in dev.excluded] + sorted(dev.included)
            ev = f"cycle {cycle}, pool {len(pool_ids)}, {dev.describe()}"
            if netted.paise:
                ev += f"; other deductions {netted}"
            if lag:
                ev += f"; assumed posting lag {lag}d (no nearer cycle reconciled)"
            return Match(b["txn_id"], "T3", REVIEW, ids, evidence=ev,
                         combinations_examined=examined)
        if budget_hit:
            return Match(b["txn_id"], "T3", EXCEPTION, [], SEARCH_BUDGET_EXCEEDED,
                         f"combination budget exhausted across {len(candidates)} candidate cycles",
                         examined)
        if bound_hit:
            code = PRIMARY_CYCLE_UNRECONCILED if primary_had_pool else DEVIATION_BOUND_EXCEEDED
            if code == PRIMARY_CYCLE_UNRECONCILED:
                ev = (f"T+{SETTLEMENT_LAG_BUSINESS_DAYS} cycle for this credit has payments "
                      f"but no deviation of size <= {self.bound} reconciles it; refused to "
                      f"attribute the credit to a more distant cycle")
            else:
                ev = (f"no deviation of size <= {self.bound} reconciles this payout in any "
                      f"of {len(candidates)} candidate cycles "
                      f"({', '.join(map(str, candidates))})")
            m = Match(b["txn_id"], "T3", EXCEPTION, [], code, ev, examined)
            if nearest_best is not None:
                resid, uniq, size, gross, cyc = nearest_best
                m.residual_paise = resid
                m.residual_unique = uniq
                m.residual_deviation_size = size
                m.pool_gross_paise = gross
                m.evidence = (ev + f"; nearest in cycle {cyc} misses by Rs "
                                   f"{resid/100:,.2f} via a "
                                   f"{'unique' if uniq else 'non-unique'} deviation of "
                                   f"size {size}")
            return m
        return Match(b["txn_id"], "T3", EXCEPTION, [], NO_RECONCILING_SET,
                     f"no payments captured in any candidate cycle "
                     f"({', '.join(map(str, candidates))})", examined)


def covered_cycles_by_matching(cascade: "Cascade", bank_rows) -> set:
    """Cycles a bank credit can be assigned to, one credit per cycle.

    Maximum bipartite matching between credits and the cycles they could belong
    to. Union-of-candidates lets one credit vouch for many cycles at once, which
    with non-injective T+2 dating makes almost every cycle look covered and
    suppresses MISSING_SETTLEMENT entirely (INC-015). A payout is one credit; the
    constraint has to be modelled.

    Simple augmenting-path matching -- the graph is tiny (tens of nodes) and the
    algorithm is deterministic given sorted inputs, which the determinism
    guarantee requires.
    """
    credits = sorted((b for b in bank_rows if b["direction"] == "CR"),
                     key=lambda b: b["txn_id"])
    options = {}
    for b in credits:
        vd = datetime.strptime(b["value_date"], "%d-%m-%Y").date()
        cands = [c for c in cascade._candidate_cycles(vd)
                 if cascade._by_capture_date.get(c)]
        options[b["txn_id"]] = sorted(cands)

    assigned = {}                    # cycle -> credit txn_id

    def augment(txn, seen):
        for cyc in options[txn]:
            if cyc in seen:
                continue
            seen.add(cyc)
            if cyc not in assigned or augment(assigned[cyc], seen):
                assigned[cyc] = txn
                return True
        return False

    for b in credits:
        augment(b["txn_id"], set())
    return set(assigned)
