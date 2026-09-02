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
