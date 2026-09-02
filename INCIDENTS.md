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
