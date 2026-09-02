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
**Still open:** which policy the project adopts is a decision for the build owner, not a
default I should pick silently. Flagged to the user at the end of Phase 01.
**Commit:** (pending — first commit of Phase 01)
