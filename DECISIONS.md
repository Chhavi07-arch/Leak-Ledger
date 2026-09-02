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

---

## ADR-002 — Self-authored sources: what independent streams do and do not buy

**Date:** 2026-09-02
**Status:** Accepted (a disclosed limitation, not a solved problem)
**Phase:** 02
**Related:** `src/leakledger/generate/observers.py` module docstring, PLAN.md
§ "The circularity that must be disclosed"

### Context

The plan review flagged that self-authored sources tend to be unconsciously
consistent: generate one canonical record, emit it three times with different
column headers, and the sources agree perfectly. A naive matcher then resolves
~100% and every downstream number is inflated.

### Decision

Separate **truth** (`world.py`) from **observation** (`observers.py`), and give
each observer four independent properties:

1. **Different visibility, not different formatting.** The bank cannot see a
   payment at all — only net money arriving. A payout is a batch net of fee, GST,
   refunds, chargebacks and reserve, so no bank row equals a payment amount except
   by coincidence. This is the structural reason N:1 subset-sum must exist.
2. **Independently-seeded RNG streams** (world 20260902, gateway 8110311, bank
   5520277, ERP 9930433), so noise in one source cannot correlate with another's.
3. **Independent mangling of shared facts.** Both bank and ERP render the same
   legal entity, each deriving from the canonical name by its own process — the
   bank uppercases, abbreviates and hard-truncates to a 35-character field; the ERP
   imitates a human typing into a form. Neither derives from the other's output.
4. **Different conventions**: date formats, identifier namespaces (RRN / UTR /
   ARN), and rounding behaviour differ because the real systems that emit them were
   built by different organisations.

**Measured result:** the naive exact-key matcher resolves **1.12%** of gateway
payments (6 of 537), against PLAN's ≤70% gate.

### What this does NOT buy — stated plainly

All three observers were written by one author. Independent streams guarantee the
noise is **uncorrelated**; they cannot guarantee it is **representative**. The
kinds of divergence present are the kinds this author thought of. Real
reconciliation is hard partly because of failure modes nobody anticipated, and no
self-authored generator reproduces that.

A second, subtler limitation: the 1.12% naive score **passed the gate trivially**.
It is low for one structural reason (bank sees only nets) that was guaranteed to
score near zero regardless of how carefully the hard cases were seeded. The gate
therefore did not discriminate. This is why a second check was added —
recoverability of true settlements by reference, measured at **52.38%** — which is
the number that actually describes the batch's difficulty. Reporting only the
1.12% would be technically accurate and substantively misleading.

---

## ADR-003 — Reconcile against the bank, not against the gateway's settlement report

**Date:** 2026-09-02
**Status:** Accepted
**Phase:** 02

### Context

A real merchant usually holds a gateway settlement report that links payments to
payouts. Including one as a fourth source would make the batch more realistic and
would give exact-reference matching (T1) substantial volume to resolve. The
generator initially carried a `settlement_utr` column on the gateway export
towards this, populated on 0 of 537 rows.

### Decision

**Do not emit a gateway settlement report.** The dead `settlement_utr` column is
removed rather than filled. Reconciliation runs gateway payments against the bank
statement, with the ERP invoice register as the third source.

### Reasoning

The gateway's own settlement report is **the artefact under audit**. A fee
overcharge, a short settlement or a missing payout are all gateway-side failures;
reconciling against a report the gateway produced would mean accepting that
gateway's account of what it owed. Deriving the expected payout independently —
from payment-level data and a versioned contract — and then testing it against
money that actually arrived in the bank is the only arrangement in which those
findings mean anything.

### Cost, accepted knowingly

The batch is harder than a typical merchant's situation, and T1 exact-reference
matching resolves less volume than it otherwise would (52.38% of paid settlements
carry a usable UTR in the bank file; the remainder must go through narration
matching or subset-sum). That difficulty is intentional and is the direct
consequence of refusing to trust the audited party's own report.

### Note for Phase 05

Seeded leak value is concentrated: 3 `MISSING_SETTLEMENT` seeds account for
roughly a third of the ₹12.04L total, because a missing payout genuinely is a
large single-ticket loss. The scorecard must therefore report **leak value per
class, a top-3 concentration figure and a median finding size** alongside the
headline, so the total is never quoted without its distribution.
