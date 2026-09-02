# Incidents

Real bugs and wrong assumptions, logged when they happen — not reconstructed at
submission time. Every entry carries a **guard**: the test that makes the defect
impossible to reintroduce. The guard is the point; anyone can describe a bug.

Format:

```
## INC-000 — one-line symptom
**Date:** YYYY-MM-DD HH:MM IST
**Phase:** NN
**Symptom:** what was observed, with the number that moved
**Root cause:** the actual mechanism, not the surface
**Fix:** what changed
**Guard added:** test name — what it asserts
**Commit:** sha
```

---

# PATTERN-01 — Tests and cases that passed without testing anything

**The single most useful thing this build taught me**, and the reason the
individual incidents below are worth reading together rather than separately.

Three times, in three different phases, a test or a seeded case existed, was
counted in ground truth or in a passing suite, looked like coverage — and
exercised nothing at all. Each was invisible in exactly the way that matters:
**the suite stayed green and the counts stayed plausible.**

| # | Where | What it claimed | What it actually did |
|---|---|---|---|
| 1 | Phase 01 — `test_composite_policy_available_and_may_differ` | that two GST rounding policies are distinguishable | asserted two things "may differ" without ever checking they *ever* do. Would have passed identically if `composite` were a copy of `per_line`. |
| 2 | Phase 03 — INC-004, `AMBIGUITY_TRAP` x6 | six seeded ambiguity traps | all six seeded both twins into the *same* payout, so the required deviation was 0 and the refusal was unreachable. Ground truth asserted six traps; zero could fire. |
| 3 | Phase 03 — INC-009, leak/adversarial collision | six reachable traps after INC-004 was fixed | two host settlements had *also* been seeded `MISSING_SETTLEMENT` and `SHORT_SETTLEMENT`, making their payouts unreconcilable by construction. The traps were dead and ground truth still counted them. |

## Why this class of defect is unusually dangerous here

A crash announces itself. A wrong number can be caught by a reviewer with a
calculator. **A test that passes without testing anything actively conceals the
gap it was written to cover** — and it does so most effectively in exactly the
places where the behaviour is hardest to get right, because that is where the
test is most likely to have been written from an assumption rather than from an
observation.

In this build the affected behaviour was `AMBIGUOUS_SUBSET`, which PLAN.md calls
the thirty seconds the whole demonstration is built around. The suite was green,
the adversarial-case count in ground truth read 40, and the single most important
behaviour in the system had never once executed.

## What actually caught each one

Not the test suite. In every case:

- **Measurement against real data**, not against an assumption. The composite
  policy was only shown to matter by sweeping 1,190,001 amounts and finding a
  29.5% disagreement. The traps were only shown to be dead by asking, of the real
  batch, *which settlement's pool contains both twins?*
- **Asking what a passing result would look like if the feature were absent.**
  Each of these tests passes just as happily with the feature removed. That
  question — not the assertion — is the actual test.

## The rule adopted for the rest of the build

> A test or seeded case is not evidence until it has been observed to **fail when
> the behaviour is absent**, or observed to **fire on real data**. A passing
> assertion and a non-zero count in ground truth are neither.

Concretely, this is why `tests/test_ambiguity_refusal.py` runs the entire cascade
over the generated files rather than constructing a fixture: an isolated unit test
passed throughout INC-004, INC-006 and INC-009, and would have gone on passing.
It also asserts *per trap* rather than on an aggregate count, because an aggregate
would have shown "4 of 6" as a pass.

---

## INC-001 — "round half-up, once, at the end" is ambiguous, and the ambiguity is not rare
**Date:** 2026-09-02 19:05 IST
**Phase:** 01
**Symptom:** PLAN.md specifies *"Rounding is half-up, once, at the end."* Implementing
GST revealed two defensible readings of "the end", and they are not equivalent.
Measured over every 1-paisa amount from Rs 100.00 to Rs 12,000.00 (1,190,001 values),
the two readings produce different GST for **29.5%** of amounts on every percentage
instrument (CREDIT_CARD 351,288 / DEBIT_CARD 351,289 / WALLET 351,254 / EMI 351,645 /
AMEX 351,296). The disagreement is always 1 paisa, but it is not an edge case.
**Root cause:** `per_line` rounds the fee to paise and then takes 18% of the *rounded*
fee, matching how a tax invoice states fee and GST as two separate rounded line items.
`composite` computes fee x 1.18 as one fraction and rounds once. Both are "half-up,
once, at the end" — they disagree about what the line item is.
**Why it matters:** the generator (Phase 02) and the GST_MISMATCH detector (Phase 04)
must use the same policy. If they diverge, ~30% of all records are flagged as GST
mismatches and the detector's precision collapses for a reason that has nothing to do
with the merchant's money.
**Fix:** made the policy an explicit, named, versioned config field
(`gst_rounding_policy`) rather than an implicit property of the code. Both readings are
implemented. The value is loaded from the fee schedule, validated against a whitelist at
load time, hashed into the run manifest, and carried on every `FeeComputation` so it
appears in the derivation string of every finding.
**Guard added:** `test_bad_gst_policy_rejected_at_load` — an unrecognised policy raises
at load rather than silently defaulting. `test_composite_policy_available_and_may_differ`
— both policies load and are distinguishable, and fee is asserted identical so only tax
rounding can differ.
**Resolved:** build owner selected `per_line` on 2026-09-02, on the grounds that GST is
levied on the taxable value as stated on the invoice line, and that stated value is
itself already rounded to paise. This is now a recorded decision rather than an
inherited default, and the policy string travels in every `FeeComputation.derivation()`
and in the run manifest's config hash. The `composite` implementation is retained so the
alternative remains testable, not deleted.
**Decision record:** the choice of `per_line` over `composite`, its real-world
justification, and the measurement table are recorded as **ADR-001** in
`DECISIONS.md` — the citable answer to "why per_line and not composite".
**Commit:** ec564340


---

## INC-002 — generator was not deterministic across processes
**Date:** 2026-09-02 20:15 IST
**Phase:** 02
**Symptom:** Running `data/generate.py` twice produced different files. Two of four
outputs diverged — `bank_statement.csv` (f07ceec7 vs 890829b7) and
`ground_truth.json` (da46dd68 vs 0048a37e) — while the gateway and ERP files were
stable. Every RNG in the generator is explicitly seeded, so the output was expected
to be byte-identical.
**Root cause:** `observers.py` built its counterparty pool as
`[c for c in {p.counterparty for p in world.payments}]` — iteration over a **set of
strings**. Python randomises string hashes per process, so the set's iteration order
differed on every invocation, and the subsequent `rng.choice()` over that list
selected different counterparties despite an identically-seeded RNG. Only the bank
observer used this construct, which is why the other two files were unaffected.
**Impact if shipped:** the determinism claim ("5 runs, one hash") would have failed
at Phase 05, and worse, ground truth would have drifted between the run that
generated the data and any later re-run — every scorecard number would have been
computed against a batch that no longer existed.
**Fix:** `sorted({...})`. Ordering is now explicit rather than incidental.
**Guard added:** `tests/test_generator_determinism.py` —
`test_identical_across_separate_processes` runs the generator twice via `subprocess`
and compares SHA-256 of all four outputs. Deliberately cross-process: within one
process set order is stable, so a same-process test would have passed while the bug
survived. Plus `test_no_unordered_set_iteration_in_generator`, a static check that
fails review if the construct reappears anywhere in `generate/`.
**Commit:** (this phase)


---

## INC-003 — engine ignored netted refunds it already had
**Date:** 2026-09-02 21:30 IST
**Phase:** 03
**Symptom:** T3 failed to reconcile 21 of 26 bank credits with
`NO_RECONCILING_SET`. Diagnosis of the residual showed 11 of 24 settlements carry
a netted refund component that the engine was not subtracting.
**Root cause:** `Cascade._deduction_for()` subtracted fee and GST only. Refunds in
`NETTED` mode reduce the payout rather than debiting separately, and
`gateway_refunds.csv` was already loaded into the engine — the data was present
and simply unused. Not a data gap; an omission in the arithmetic.
**Fix:** added `_netted_refunds_for_cycle()` and folded it into the deduction
closure passed to the search. Netted refunds are constant across deviations, so
they are computed once per credit rather than per candidate.
**Result:** `NO_RECONCILING_SET` fell 21 -> 18, review-queue matches rose 6 -> 9.
**Guard added:** pending — a per-cycle arithmetic test asserting that a settlement
with a netted refund reconciles, and fails if the refund term is dropped.
**Commit:** (this phase)

---

## INC-004 — the ambiguity trap could never fire
**Date:** 2026-09-02 21:50 IST
**Phase:** 03
**Symptom:** `AMBIGUOUS_SUBSET` did not fire once across the whole batch, despite
6 seeded `AMBIGUITY_TRAP` cases. The refusal is the behaviour PLAN.md calls "the
thirty seconds the whole video is built around", so a silent zero here is the most
expensive possible false negative.
**Root cause:** the trap seeded two payments with identical amounts at 11:00 and
12:00 on the same day — so **both landed in the same payout**. Ambiguity can only
arise when the engine must EXCLUDE one of two indistinguishable payments; with
both inside the payout the required deviation is 0 and there is nothing to choose
between. The adversarial case was decorative: it looked adversarial in ground
truth and exercised nothing.
**Why it survived:** the case was written against PLAN's original subset-sum
framing ("find a subset summing to the payout"), where two identical amounts in
the pool are genuinely ambiguous. Once T3 was correctly reformulated as a
*deviation* search (see ADR-004), that framing no longer produced ambiguity, and
the seeded case was not revisited.
**Fix:** one twin is now captured at 23:40 so it rolls into the next payout. Both
share a capture DATE, so the engine pools both and must exclude exactly one — and
since the amounts are identical, either exclusion reconciles.
**Verified:** all 6 traps now split across payouts (`stl_0003/stl_0004`,
`stl_0020/stl_0021`, ...), which is the precondition for the refusal.
**Still open:** the refusal STILL does not fire, because those settlements fail
earlier with `NO_RECONCILING_SET` — the search returns no solution at any
deviation size before it can discover multiple. Tracked as the outstanding item
for Phase 03; the precondition is fixed, the trigger is not yet reached.
**Guard added:** pending — a real-data test asserting `AMBIGUOUS_SUBSET` fires on
at least the seeded traps. Deliberately not written as a unit test in isolation:
an isolated test would have passed throughout this incident.
**Commit:** (this phase)


---

## INC-005 — cycle inference assumed an invertible map; it is not injective
**Date:** 2026-09-02 22:20 IST
**Phase:** 03
**Symptom:** 4 of 9 genuine T3 failures were settlements whose cycle date fell on a
Saturday. The engine inferred the preceding Friday and searched the wrong pool.
**Root cause:** the generator dates a payout `add_business_days(cycle, 2)`. That
map is **not injective**: a Friday cycle and a Saturday cycle both land on the
following Tuesday, because the forward walk skips the weekend either way.
`_infer_cycle_date()` did a single backward walk and therefore could never return
a non-business-day cycle. Cycle dates land on Saturdays routinely, because a
Friday 23:xx capture rolls into Saturday.
**Fix:** replaced inversion-by-walking with `_candidate_cycles()`, which returns
**every** date whose forward T+2 mapping equals the observed value date. The engine
tries each and lets the arithmetic decide. If two candidate cycles reconcile
identically, that is genuine ambiguity and is refused rather than resolved by
preferring the earlier date.
**Second fix in the same change:** split `NO_RECONCILING_SET` into two codes.
`DEVIATION_BOUND_EXCEEDED` means a reconciling set may exist but not within the
bound this engine agreed to search — a principled refusal. `NO_RECONCILING_SET`
now means no candidate pool existed at all. Conflating them overstated what had
actually been ruled out.
**Guard added:** pending — round-trip property test asserting every cycle date in
ground truth appears in `_candidate_cycles(its value_date)`.
**Commit:** (this phase)

---

## INC-006 — the T3 bound was chosen from a measurement that counted one direction
**Date:** 2026-09-02 22:40 IST
**Phase:** 03
**Symptom:** `AMBIGUOUS_SUBSET` fired zero times even after INC-004 and INC-005
were fixed. The settlements holding ambiguity traps were being abandoned before
the refusal could trigger.
**Root cause:** the bound `d <= 3` was chosen from a measurement I took before
writing the search, which counted only *payments settling in a cycle other than
their capture date* — max 3, mean 0.88. That is one direction. The deviation the
search actually performs is **exclusions + inclusions**, and a straddling payment
counts **twice**: once excluded from its capture-date pool, once included into its
settlement-date pool. True distribution, measured against ground truth:
`{0:1, 1:8, 2:9, 3:2, 4:1, 5:3, 6:1, 7:1}` — **6 of 26 settlements need d > 3**,
including `stl_0003` and `stl_0004`, which hold the ambiguity traps.
**Consequence:** a bound presented as "covers every observed case with zero
headroom" in fact covered 20 of 26, and structurally prevented the single most
important behaviour in the build from ever executing. The number was real; it
measured the wrong quantity.
**Measured effect of the bound (42 bank rows):**

| bound | time | combinations | AMBIGUOUS_SUBSET | DEVIATION_BOUND_EXCEEDED | SEARCH_BUDGET_EXCEEDED |
|---|---|---|---|---|---|
| 3 | 1.8s | 64,105 | **0** | 14 | 0 |
| 4 | 13.9s | 493,035 | **1** | 13 | 0 |
| 5 | 33.9s | 1,314,440 | **1** | 10 | 3 |

**Fix:** not yet applied — the bound is a build-owner decision and was flagged
rather than changed unilaterally.
**Guard added:** pending — a real-data test asserting `AMBIGUOUS_SUBSET` fires on
the seeded traps, which is the test that would have caught this on day one.
**Commit:** (this phase)


---

## INC-007 — one day of bank posting lag made the true pool unreachable at any bound
**Date:** 2026-09-02 23:10 IST
**Phase:** 03
**Symptom:** Two settlements holding ambiguity traps (`stl_0009` d=2, `stl_0024`
d=3) failed with `NO_RECONCILING_SET` despite needing deviations well inside the
bound. Tracing showed ground truth dating `stl_0009` at value date 2026-06-16
while the bank row carried 2026-06-17.
**Root cause:** the bank observer models a 5% posting lag (`value_date + 1 day`),
which is realistic — banks do post late. The engine computed candidate cycles from
the observed value date alone, so a single day of lag shifted the inferred cycle
wholesale and the correct pool was never examined. No deviation bound can recover
from searching the wrong pool, which is why this presented as a bound problem.
**Fix:** `POSTING_LAG_TOLERANCE_DAYS = 2`. Candidate cycles are computed for a
window of plausible release dates rather than for the observed date alone.
**Result:** both settlements immediately reached AMBIGUOUS with 2 solutions each.
**Guard added:** covered by `tests/test_ambiguity_refusal.py`, which asserts the
refusal fires on the real batch — these two traps are part of that population.
**Commit:** (this phase)

---

## INC-008 — the search had a hidden performance ceiling that suppressed correctness
**Date:** 2026-09-02 23:30 IST
**Phase:** 03
**Symptom:** The deviation bound was being chosen by runtime rather than by the
data. At d<=4 the batch took 13.9s, at d<=5 33.9s, and once INC-007 widened the
candidate-cycle set the full run exceeded a two-minute timeout. Because the
settlements holding ambiguity traps require d=4 and d=5, a bound picked for speed
silently prevented the build's headline behaviour from ever executing.
**Root cause:** v1 of `search_deviation` enumerated combinations directly —
O(C(n,d)) — over pools of up to 42 payments. C(42,5) = 850,668 per cycle, times
several candidate cycles, times 43 bank rows.
**Why this is worth recording:** the defect never produced a wrong answer. It
produced a *bound*, and the bound produced a wrong answer. A performance ceiling
that expresses itself as a correctness gap is far harder to notice than a crash,
because every individual number looks defensible.
**Fix:** rewrote the search around the observation that deductions are per-payment
and additive, so a payment's contribution is a single integer
`value = amount - fee - gst` and reconciliation reduces to a signed, size-bounded
subset-sum: `sum(included) - sum(excluded) == delta`. Neighbour sets are tiny and
enumerated directly; the pool side is solved by meet-in-the-middle, splitting the
pool in half, indexing size-bounded subset sums of each half and joining on the
required complement. C(42,5) = 850,668 becomes 2 x C(21,<=5) = 55,792, and the
join yields exact solution COUNTS, which is precisely what ambiguity detection
needs.
**Measured effect (43 bank rows, same data):**

| bound | v1 time | v2 time | speedup | AMBIGUOUS_SUBSET |
|---|---|---|---|---|
| 3 | 1.8s | 0.05s | 36x | 0 -> 2 |
| 4 | 13.9s | 0.13s | 107x | 1 -> 5 |
| 5 | 33.9s | 0.36s | 94x | 1 -> 5 |
| 6 | (timeout) | 0.87s | — | 5 |

**Second finding from the rewrite:** a wider bound is NOT strictly better. At d<=7
the refusal count rises from 5 to 9 as coincidental alternative reconciliations
appear — the engine begins refusing matches it should make. The bound is therefore
set at 6: it covers every true deviation in the data (max 6) and sits just below
where spurious ambiguity begins. Recorded as ADR-004.
**Guard added:** `tests/test_ambiguity_refusal.py` — five real-data assertions
including that both refusal mechanisms (within-cycle and across-candidate-cycle)
are exercised.
**Commit:** (this phase)

---

## INC-009 — seeded leaks silently cancelled seeded adversarial cases
**Date:** 2026-09-02 23:50 IST
**Phase:** 03
**Symptom:** After INC-007 and INC-008, four of six ambiguity traps fired. The
remaining two were traced to their host settlements: `stl_0021` had been seeded
`MISSING_SETTLEMENT` (so no bank credit exists at all) and `stl_0020` seeded
`SHORT_SETTLEMENT` (the payout is deliberately short by Rs 180.11 and therefore
cannot reconcile by construction). In both cases the engine was behaving
correctly; the trap was dead.
**Root cause:** `seed_payment_cases` and `seed_settlement_cases` chose their
targets independently, with no mutual exclusion. Ground truth continued to assert
that six ambiguity traps were present, so any scorecard computed against it would
have counted two cases the engine could not possibly satisfy — understating
performance for a reason that was an artefact of the generator.
**Same class as INC-004:** an adversarial case that exists in ground truth and
exercises nothing. Third occurrence of this pattern in this build.
**Fix:** `seed_settlement_cases` now takes `protected_payment_ids` and excludes any
settlement hosting an adversarial case from destructive leak classes.
**Result:** 6 of 6 traps fire.
**Guard added:** `test_every_reachable_trap_host_is_refused` asserts at most one
trap may be unreachable and at least five must be, so a regression in seeding is
caught rather than absorbed.
**Commit:** (this phase)


---

## INC-010 — an exception was being counted as a leak, inflating the headline 3x
**Date:** 2026-09-03 00:40 IST
**Phase:** 04
**Symptom:** `MISSING_SETTLEMENT` reported 17 findings covering **270 payments**
worth Rs 22.4L. Ground truth contains 3 missing settlements covering 76 payments.
**Root cause:** the rule flagged every captured payment the cascade had not
positively matched. That swept in every payout the cascade had *refused* --
ambiguous subsets, deviation-bound exceedances -- and counted them as lost money.
**194 of the 270 flagged payments had settled perfectly well;** the engine simply
could not prove it. The headline leakage figure was inflated roughly 3x by
reporting ignorance as loss.
**Why this one matters most:** it is the exact failure the project exists to
argue against. A reconciliation tool that reports what it could not resolve as
money that is gone is worse than no tool, because the number is confident and
wrong. Total findings fell from Rs 25.3L to Rs 8.4L once corrected.
**Fix:** the rule now requires POSITIVE EVIDENCE OF ABSENCE. A payout is missing
only if no *unspoken-for* bank credit could belong to its cycle, tested with the
tightest (zero posting-lag) inversion. Where a credit exists but cannot be
reconciled, that is an exception and belongs in the exception queue.
**Honest residual:** precision 0.60, recall 1.00. Two cycles are still
over-claimed. Measured separately: only 1 of the 3 truly-unpaid cycles is
provable by date compatibility alone at ANY lag tolerance -- the other two are
shadowed by neighbouring credits. Distinguishing a missing payout from a
mis-attributed one is genuinely hard with payments and a bank statement only, and
that limit is a property of the data, not of the rule.
**Guard added:** `test_missing_settlement_never_claims_a_merely_unmatched_payout`
asserts every such finding states "NO bank credit", so a regression to the
unmatched-equals-missing logic fails the suite.
**Commit:** (this phase)

---

## INC-011 — a detector keyed on a signal the engine never emits (PATTERN-01, 4th)
**Date:** 2026-09-03 00:55 IST
**Phase:** 04
**Symptom:** `SHORT_SETTLEMENT` reported 0 findings against 2 seeded. Recall 0.00.
**Root cause:** the detector filters on `m.reason_code == "SHORT_RESIDUAL"`. The
cascade emits no such code -- it never has. The detector is unreachable code that
imports cleanly, runs without error, and can never fire.
**Why it is logged rather than quietly fixed:** this is the fourth occurrence of
PATTERN-01 in this build, and the first one I introduced *after* naming the
pattern, in code written the same day. That is worth recording precisely because
it shows the failure mode is not a lapse in attention that awareness fixes -- the
detector was written from an assumption about what the cascade produced, and
nothing about writing it felt different from writing the nine that work.
**Status: OPEN.** Detecting a short settlement requires knowing the correct pool
first, and for the settlements in question the cascade cannot determine the pool.
The honest options are (a) extend the search to report a nearest-residual
reconciliation when no exact one exists, or (b) remove the detector and record
SHORT_SETTLEMENT as undetectable with payment-plus-bank data alone. Not decided
unilaterally; flagged to the build owner.
**Guard needed:** a test asserting each registered detector can fire at least once
on the batch -- which would have caught this immediately, and would have caught
INC-004 and INC-009 as well.
**Commit:** (this phase)


---

## INC-012 — a short payout caused a FALSE MATCH, the metric the build exists to protect
**Date:** 2026-09-03 02:10 IST
**Phase:** 04
**Symptom:** Auditing every T3 match against ground truth found **1 wrong set in
15**. `BNK000007` was matched to 20 payments with **zero overlap** against the true
29. False-match rate 6.7%, not 0.
**Root cause:** `stl_0006` was seeded short by Rs 52.14, so its own cycle could not
reconcile exactly. The engine then searched more distant candidate cycles and
found a *spurious exact* reconciliation four days away. All candidate cycles were
treated as equally plausible, so a distant coincidence beat a near-miss. A genuine
defect in one cycle was converted into a confident wrong answer about another.
**Why it is the most serious defect so far:** false-match rate is the primary
metric of the whole submission, and every individual step looked correct. The
search did find an exact reconciliation; it was simply an exact reconciliation of
the wrong thing.
**Fix, in two parts.** (1) Candidates are tried in posting-lag order, nearest
first. (2) If the credit's own T+2 cycle has payments but does not reconcile, the
engine **stops** and emits `PRIMARY_CYCLE_UNRECONCILED` rather than searching
further out -- the likely explanation is a defect in that cycle, not a four-day
lag. Lag ordering alone was measured and did NOT fix it: when the correct answer
is impossible, some wrong answer will always beat no answer. Refusing is the only
correct response.
**Result:** false matches 1 -> **0**. Honest cost: correct T3 matches fell 14 -> 13
(one legitimate lagged match now refused) and ambiguity traps firing fell 6 -> 5.
**Guard added:** the false-match audit is the primary Phase 05 metric and must run
in CI.
**Commit:** (this phase)

---

## INC-013 — SHORT_SETTLEMENT is detectable but NOT quantifiable
**Date:** 2026-09-03 02:35 IST
**Phase:** 04
**Question asked:** can a nearest-residual reconciliation support a
short-settlement claim under a positive-evidence bar equivalent to the one INC-010
imposed on MISSING_SETTLEMENT?
**Measured answer: no.** The residual depends entirely on the deviation bound,
because the search absorbs the shortfall by re-attributing payments. Against two
seeded shortfalls of Rs 52.14 and Rs 114.60:

| settlement | true short | d=0 | d=1 | d=2 | d=6 |
|---|---|---|---|---|---|
| stl_0006 | Rs 52.14 | Rs 21,850.20 | Rs 621.16 | **Rs 52.14** | Rs 0.01 |
| stl_0017 | Rs 114.60 | Rs 6,223.08 | Rs 841.09 | Rs 146.63 | Rs 0.04 |

d=2 reproduces one seeded value **exactly** and misses the other by 28%. Selecting
d=2 for residual reporting while matching at d=6 would be tuning a parameter until
a number matched ground truth -- the same error class as INC-010, and precisely
the self-grading this build argues against. It was not done.
**Decision:** the detector fires on `PRIMARY_CYCLE_UNRECONCILED` and reports value
**ZERO**, stating explicitly that the shortfall is unquantified and why. It
contributes nothing to the headline total and everything to the exception queue.
**Second, worse finding:** the signal is not specific. `PRIMARY_CYCLE_UNRECONCILED`
fires on **10 credits, only 2 of which are seeded short settlements** -- the rest
fail to reconcile for unrelated reasons. So this is not a short-settlement
detector at all; it is an unreconciled-cycle flag. **Recommendation: demote
SHORT_SETTLEMENT from a leak class to an exception type.** Flagged, not done
unilaterally.
**Commit:** (this phase)
