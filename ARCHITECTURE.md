# Architecture

How Leak Ledger is put together, and — more usefully — the constraints that
shaped it. Every non-obvious choice here was made because a measurement forced
it; the incident or decision record is cited so the reasoning survives outside
the session it was made in.

---

## The pipeline

```
  SOURCES        gateway_payments.csv      (payment level, ISO+05:30, RRN)
                 gateway_refunds.csv       (refund level, ARN)
                 gateway_adjustments.csv   (disputes, reserve holds/releases)
                 bank_statement.csv        (settlement level only, DD-MM-YYYY, UTR)
                 erp_invoices.csv          (invoice level, human-typed names)
                          │
  ┌───────────────────────▼─────────────────────────────────────────┐
  │ 1  INGEST      schema validation · typed quarantine, never drops │
  │                money → integer paise · timestamps → IST-aware    │
  ├──────────────────────────────────────────────────────────────────┤
  │ 2  CASCADE     T1 exact reference (ARN)                          │
  │                T2 unique amount + date window                    │
  │                T3 settlement deviation search  ← the hard tier   │
  │                T4 narration proposal, arithmetic-verified  ◆     │
  │                T5 typed exception                                │
  ├──────────────────────────────────────────────────────────────────┤
  │ 3  DECOMPOSE   fee · GST · TDS · reserve, against versioned      │
  │                contract; every result carries its derivation     │
  ├──────────────────────────────────────────────────────────────────┤
  │ 4  LEAKAGE     ten typed detectors over reconciled state         │
  ├──────────────────────────────────────────────────────────────────┤
  │ 5  LEDGER      double-entry · idempotent apply · append-only     │
  ├──────────────────────────────────────────────────────────────────┤
  │ 6  REPORT      scorecard · exception queue · HTML  ◆ rationale   │
  └──────────────────────────────────────────────────────────────────┘

  ◆ = the only places a model is invoked. Neither can change a number.
```

---

## What the engine is deliberately *not* allowed to know

This is the single most important design constraint, and it exists because
without it the whole thing scores well while proving nothing.

The generator assigns each payment to a settlement cycle using
`cycle_date_for_capture()`, which applies a 23:00 IST cutoff. **The engine is
never given that function.** It infers a cycle from the bank value date by
business-day arithmetic — which it legitimately can do — and pools payments by
their *calendar capture date*. Payments captured late in the evening may have
rolled into a neighbouring payout, and the engine must discover that by search.

Hand it `cycle_date_for_capture()` and the candidate pool becomes correct by
construction: the deviation search would find nothing to do, report a perfect
match rate, and demonstrate nothing at all.

---

## T3: why this is not a subset-sum

PLAN framed T3 as *"find a subset of payments summing to this payout, bounded at
k ≤ 12"*. Measured against the real batch, that framing is wrong: settlements
carry a **median of 22 payments and up to 37**, so the true answer is almost
always the *whole* pool, not a small selection from it. A size-bounded subset
search can never find it, and every large settlement would emit a budget
exception — reading as principled restraint while actually being a search aimed
at the wrong problem.

So T3 searches for a **deviation**: presume the full cycle, then find the small
set of exclusions and inclusions that reconciles the payout.

The reduction that makes it tractable: deductions are per-payment and additive,
so a payment's contribution is one integer `value = amount − fee − gst`, and
reconciliation becomes a signed, size-bounded subset-sum:

```
sum(included) − sum(excluded) == delta
```

Neighbour sets are tiny and enumerated directly; the pool side is solved
**meet-in-the-middle** — split the pool, index size-bounded subset sums of each
half, join on the required complement. `C(37,5) = 435,897` becomes
`C(18,≤5) + C(19,≤5) = 29,280` — about a 15x reduction — and the join yields
exact **solution counts**, which is
what ambiguity detection needs (ADR-004, INC-008).

**Bound `d ≤ 6`**, from the measured deviation distribution over paid
settlements `{0:1, 1:8, 2:9, 3:2, 4:1, 5:2, 6:1}`. Wider is *not* better: at
`d ≤ 7` the refusal count rises 5 → 9 as coincidental alternative reconciliations
appear and the engine starts refusing matches it should make.

### Refusal, not resolution

Two rules produce every refusal in the system, and both were forced by measurement:

- **Ambiguity.** Every solution at the minimum deviation size is enumerated, not
  the first one found. If two or more distinct deviations reconcile the same
  payout, the engine raises `AMBIGUOUS_SUBSET` and matches nothing. Returning the
  first hit is the most common way to manufacture a silent false match.
- **Primary cycle.** If a credit's own T+2 cycle has payments but will not
  reconcile, the engine stops rather than searching more distant cycles. Ordering
  candidates by posting lag was tried first and **measured not to work**: when the
  correct answer is impossible, some wrong answer always beats no answer. This
  rule took false-match rate from 6.7% to 0 (INC-012).

T+2 dating is also **not injective** — a Friday and a Saturday cycle both land on
the following Tuesday — so cycle inference returns a *candidate set*, not a date,
and two candidate cycles reconciling identically is itself an ambiguity (INC-005).

---

## The AI boundary

The architecture's claim is that a model **proposes** and arithmetic **disposes**.
That claim is only worth making if it is tested with proposals chosen by an
adversary rather than by the model.

| Call site | What constrains it |
|---|---|
| Narration → candidate entities | Returns candidates. Accepted only if the named reference **exists** and the amount **agrees exactly** — no tolerance band. Confidence is never read. |
| Exception rationale | Discarded if it introduces a figure absent from the evidence; the deterministic string is shown instead. |
| Settlement Q&A | Read-only. Accepts a facts dict, not the engine — no write path exists to remove. |

Refused: the match decision, any arithmetic, leakage classification, any ledger
write.

`AdversarialProvider` returns confidently wrong, well-formed answers. With it
wired in, **every metric is byte-identical and false-match rate stays 0.0000**. A
boundary that depends on the model behaving is not a boundary. And it is tested in
both directions, because a gate that rejects everything holds trivially: a
*correct* proposal at confidence **0.30** is accepted; a *wrong* one at **0.99** is
rejected.

---

## Correctness properties, all asserted in CI

| Property | Assertion |
|---|---|
| Integer paise | Floats rejected at construction; half-up away from zero, never banker's rounding |
| Timezone | Naive datetimes rejected; `SETTLEMENT_CUTOFF_IST` named |
| Determinism | 5 runs → 1 hash |
| Idempotence | Δ ledger = 0 on second apply |
| Double entry | Trial balance = ₹0.00 |
| No silent loss | rows in == records + quarantined; every exit typed |
| False-match rate | Pinned at ceiling 0.0, with a floor on scoreable matches |
| Ground-truth consistency | 672 checks run *before* any scoring |

The guards are **mutation-tested**: disabling the primary-cycle fix reproduces
false-match rate 0.0667 exactly; breaking idempotence fails the delta test.

### Why ground truth is validated as a first-class artefact

A self-contradictory fixture silently penalises a correct engine and can reward an
incorrect one, and no amount of testing the engine detects it — the engine is
graded by the very thing that is broken. A settlement was once seeded whose stated
net contradicted its own components, and the engine was correctly refusing to
reconcile it while looking like a regression (INC-014).

---

## What the numbers mean

**Claimed value sums every reported instance, not only the verified ones** — that
is what the engine claims at run time, having no ground truth of its own.
Precision is published beside it, and any class with FP > 0 is excluded from the
confirmed headline. The distinction is not cosmetic: 69% of the gross figure comes
from classes measuring below 1.00 precision, and quoting the gross alone overstates
confidence (INC-017).

Two classes are weak and say so: `MISSING_SETTLEMENT` at precision 0.20, capped by
an information limit proven three ways (INC-015); `SHORT_SETTLEMENT`, detectable
but not quantifiable, claiming no rupee value at all (INC-013).

---

## Known limits

- Reads CSVs, not live APIs.
- The fee schedule is hand-authored config. Inferring effective rates from
  observed settlements is the harder problem and is not attempted — which is why
  the three contract-dependent detectors are **verification, not discovery**
  (ADR-001).
- No multi-user review workflow, no approval audit, no segregation of duties.
- Batch, not continuous. Late-arriving files and cross-cycle corrections are not
  modelled.
- The three sources are self-authored. Independent RNG streams guarantee the noise
  is *uncorrelated*; they cannot guarantee it is *representative* (ADR-002).
- **TDS is not implemented.** The settlement identity in the original plan included
  a TDS withholding term (marketplace / 194-O). It is described in the identity
  above and in comments, but no code computes it and the fee schedule carries no
  TDS rate. Settlements in the generated batch have no TDS component, so nothing
  is silently wrong — the term is simply absent.
- **REVERSAL_PAIR was never built.** A debit and credit against the same reference
  netting to zero, which must not be counted as two matches, was one of eight
  declared hard-case types. It is not seeded and no detector looks for it. The
  other seven are seeded and traceable in ground truth.
- **No T0 tier exists in the cascade.** The design describes T0 as
  canonicalise/dedupe; in practice deduplication happens inside
  `detect_duplicate_capture` rather than as a distinct matching tier, so the
  emitted tiers are T1-T5.
