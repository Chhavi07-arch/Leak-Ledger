# Decision records

Choices that a reader could reasonably have made differently, with the reasoning
that settled them. Separate from `INCIDENTS.md`: incidents are things that broke,
decisions are forks that were taken deliberately.

Each record states the alternative that was rejected and **what it would have
cost**, so the reasoning survives outside the session it was made in.

---

## ADR-001 — GST is computed per invoice line, not on an unrounded intermediate

**Date:** 2026-09-02
**Status:** Accepted
**Phase:** 01
**Related:** INC-001 (how the ambiguity surfaced), `config/fee_schedule.v1.json`
(`gst_rounding_policy`), PLAN.md § "Rounding and the GST policy"

### Context

PLAN.md specified only *"Rounding is half-up, once, at the end."* Implementing GST
showed that "the end" has two defensible readings, and that they are not
equivalent in practice.

- **`per_line`** — round the gateway fee to paise, then GST = round(18% of that
  **rounded** fee).
- **`composite`** — compute fee x 1.18 as a single fraction and round once at the
  very end, deriving GST as the residual.

### The measurement that made this a decision rather than a detail

Evaluated over every one-paisa amount from Rs 100.00 to Rs 12,000.00
(1,190,001 values), on each percentage instrument in the schedule:

| Instrument | Rate | Amounts where the two policies disagree | Share |
|---|---|---|---|
| CREDIT_CARD | 200 bps | 351,288 | 29.52% |
| DEBIT_CARD  | 90 bps  | 351,289 | 29.52% |
| WALLET      | 180 bps | 351,254 | 29.52% |
| EMI         | 250 bps | 351,645 | 29.55% |
| AMEX        | 280 bps | 351,296 | 29.52% |

The disagreement is always exactly one paisa, but it occurs on roughly **three
amounts in ten** — not an edge case. Since `GST_MISMATCH` fires on any deviation
from the expected tax line, the policy choice moves approximately **30% of
GST_MISMATCH classifications**. Had the generator and the detector been written
under different readings, ~30% of records would have been flagged as tax mismatches
for a reason having nothing to do with the merchant's money.

### Decision

**Adopt `per_line`.**

A tax invoice states the gateway fee and the tax on it as two separate line items,
each already rounded to paise, and GST is levied on the taxable value **as stated
on that line**. `per_line` reproduces the artefact a finance associate would
actually hold in their hand and check against. It has a real-world referent.

### Alternative rejected, and why

`composite` is arguably the more literal reading of "round once, at the end", and
it is the mathematically tidier operation. It was rejected because **no invoice is
stated that way**, so there is nothing external to validate it against. Choosing it
would mean the "correct" GST for every record was defined solely by a convention
invented inside this repository.

`composite` remains implemented and loadable, so the alternative stays testable and
the claim above stays falsifiable. It was not deleted.

### Honest note on what this decision does *not* fix

Selecting `per_line` gives GST a real-world referent for the *rounding rule*. It
does not remove the circularity disclosed in PLAN.md: the 18% rate and the fee
slabs are still authored in this repository, and the generator still produces data
from the same file the detector checks against. `GST_MISMATCH` therefore remains
**verification, not discovery** — and is in fact the most circular of the three
contract-dependent rules, because after this decision its notion of "correct" is a
convention chosen here rather than a rate with an external source. That should be
stated in the README alongside the rule, not left for a reviewer to notice.

### How it is enforced

- `gst_rounding_policy` is a field in the versioned fee schedule, not a constant in
  code.
- Validated against a whitelist at load time; an unrecognised value raises rather
  than silently defaulting (`test_bad_gst_policy_rejected_at_load`).
- Carried on every `FeeComputation` and printed in its `derivation()` string, so
  every finding shows which policy produced it.
- The schedule is content-hashed into the run manifest, so any reported number is
  reproducible against the exact policy it was computed under.
