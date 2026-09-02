# Leak Ledger — Build Plan

**Razorpay AI Buildathon · Track 04, AI Finance Controller**
Solo build · submission 5 September 2026 · plan revised 2 September 2026

A reconciliation engine that reports what the books are *hiding*, not what they
matched. Deterministic where money is decided, AI only where language is
ambiguous, honest about everything it could not resolve.

---

## Who this is for, and what it replaces

**The user is a finance-operations associate** at a mid-size D2C or marketplace
merchant processing roughly 5,000–50,000 transactions a month. They report to a
finance controller. They are not an engineer and they live in a spreadsheet.
Today, on settlement day, they download the gateway settlement report and the
bank statement, paste both into Excel, sort by amount and date, and eyeball the
difference. When the totals don't tie they work backwards by hand. Whatever never
resolves goes onto a "pending" tab that grows month over month and is written off
at quarter end as *unreconciled differences*. **That write-off is the leakage, and
nobody ever finds out what it was.** Leak Ledger replaces the spreadsheet and the
eyeballing: it pairs the records itself, recomputes every fee and tax line against
a versioned contract, and turns the pending tab from an untyped lump that grows
into a typed queue that shrinks — every item carrying its own arithmetic and a
specific next action.

### The workflow it creates

| When | Time | What they open | What they do |
|---|---|---|---|
| **Daily** | ~5 min | Run summary | Overnight run on yesterday's files. Three numbers: matched, in review, exceptions. If review and exceptions are both empty, close the tab. |
| **Settlement day / weekly** | 30–45 min | Review queue | Work each proposed match: the pairing, its arithmetic, its tier. Approve or reject. Rejections are captured as labelled data. |
| **Monthly** | ~1 hr | Findings report | Goes to the controller. Triggers a claim against the gateway, or a write-off decision — but now itemised by cause instead of a lump sum. |

**What they stop doing:** building the reconciliation spreadsheet from scratch
each cycle, and carrying an untyped pending tab they cannot explain to the
controller.

### Why an auto-applied match can go unreviewed

This is the question the whole design has to answer, and the honest answer is
**not** "because the confidence score is high."

Tier scores in this system are **ordinal labels assigned by rule, not calibrated
probabilities.** Tier 2 is not "90% likely to be correct" — nothing in this build
establishes that, and presenting it as a probability would be a claim the harness
cannot support. Three structural properties are what actually justify skipping
review:

1. **Tier 1 is a lookup, not an inference.** An exact reference match with a
   corroborating amount contains no judgement. It is wrong only if the source
   data is wrong, and no tool saves you from that.
2. **Tier 2 auto-applies only under a uniqueness constraint.** If more than one
   candidate exists in the window, it does not auto-apply — it becomes an
   exception. The auto-apply population is defined by *absence of alternatives*,
   not by a score.
3. **The real safety net is reversibility, not accuracy.** Every auto-apply is
   written to an append-only log with its full evidence, and apply is idempotent,
   so a corrected re-run reverses cleanly. Skipping review is acceptable because
   being wrong is *detectable and recoverable*, not because being wrong is
   impossible.

**Failure cost when it is wrong and nobody checks:** a payment is marked settled
that never settled. It leaves the exception queue. Nobody chases it. The merchant
silently absorbs it. That is precisely the loss this tool exists to prevent — a
false match is the tool committing the original sin it was built to catch. This
is why false-match rate is the primary metric and why ambiguous subsets are
refused rather than resolved.

**Calibration is future work, and should be named as such:** turning tier labels
into real probabilities requires reviewer accept/reject outcomes accumulated over
time. That is the correct answer to "what would you do next," not more detectors.

---

## Honest scope — what this is and is not

Stated plainly in the README, because claiming less is the credibility move.

**What it is:** a working reconciliation and leakage-detection engine for a single
merchant with a known contract, demonstrated on a 500-record synthetic batch with
published ground truth.

**What it is not, and what adoption would require:**

- **Real source connectors.** It reads CSVs. Production needs gateway APIs, bank
  statement ingestion, and ERP integration.
- **Contract ingestion.** The fee schedule is hand-authored config. Real merchants
  do not have a machine-readable contract, and inferring effective rates from
  observed settlements is a genuinely harder problem this build does not attempt.
- **Multi-user review workflow.** No accounts, no approval audit trail, no
  segregation of duties — all of which a finance team would require.
- **Continuous operation.** It runs as a batch. Reconciliation in production is
  continuous, with late-arriving files and cross-cycle corrections.

### Rounding and the GST policy — a decision, not a detail

Money is integer paise throughout and rounding is **half-up, away from zero**.

For GST specifically the project uses **`per_line`**: GST is computed on the fee
*as it would appear on a real invoice line* — that is, on the fee already rounded
to paise — not on an unrounded intermediate. A tax invoice states the fee and the
tax as two separate, separately-rounded line items, and GST is levied on the
taxable value as stated. `per_line` reproduces that; it has a real-world referent.

The alternative reading, **`composite`** (compute fee x 1.18 as one fraction and
round once at the very end), was implemented, measured, and rejected. It is a more
literal reading of "round once at the end", but no invoice is stated that way, so
there is nothing to validate it against.

**This is not an implementation detail.** Measured over every one-paisa amount from
Rs 100.00 to Rs 12,000.00, the two policies disagree on **29.5%** of amounts — always
by one paisa, but on roughly three amounts in ten. The choice therefore moves about
30% of `GST_MISMATCH` classifications. Both implementations are retained so the
alternative stays testable; the active policy is a versioned config field that
travels in every finding's derivation string and in the run manifest.

Recorded as **ADR-001** in `DECISIONS.md`, and as **INC-001** in `INCIDENTS.md`
(the ambiguity was found during Phase 01, not anticipated in the plan).

---

### The circularity that must be disclosed, not hidden

`FEE_OVERCHARGE`, `GST_MISMATCH` and `ZERO_MDR_VIOLATION` are **verification, not
discovery.** The fee schedule is authored here, the data is generated from it, and
the detector compares against it. These rules prove the arithmetic and the
plumbing are correct. They do not prove the system can find something nobody told
it about. Say this before a reviewer says it.

Consequently:

- **The headline leads with structural findings** — `MISSING_SETTLEMENT`,
  `DUPLICATE_PAYOUT`, `DUPLICATE_CAPTURE`, `REFUND_NOT_REACHED`,
  `CHARGEBACK_NOT_RECREDITED`. These detect money loss **without needing to know a
  contract rate**, which makes them the structurally sound half of the taxonomy.
- **Fee findings are reported second**, explicitly labelled contract-dependent.
- **`ZERO_MDR_VIOLATION` is reported as a rule check, not a detection result**, and
  is excluded from aggregate precision/recall. Its precision is 1.0 by
  construction; including it inflates the scorecard with a free win, and a
  reviewer who notices that discounts every other number on the page.

---

## Metrics

The brief's bar for this track names three things: *throughput plus measured
accuracy plus an honest exception list.* All three get reported.

### Primary

| Metric | Shape | Notes |
|---|---|---|
| **False-match rate** | `0 / N auto` | Wrong pairings ÷ auto-applied, against ground truth. Reported separately for the adversarial population. Lead every artefact with this. |
| Auto-match rate | % by tier | Broken out by tier so the reviewer sees where volume actually resolves. |
| Review-queue rate | % | Not a failure. Deliberate restraint has a number. |
| Exception rate | % + distribution | Full breakdown across reason codes. |
| Leakage precision / recall | per class | Per reason code, never aggregate. Structural and contract-dependent classes reported in separate blocks. |
| Leakage value | `₹ found / ₹ seeded` | Recall denominated in money. Structural classes first. |
| Throughput | records/sec | End-to-end, and again with the model layer disabled — the deterministic core should be dramatically faster, which is itself an argument. |
| Determinism | 5 runs → 1 hash | Asserted in a test. |
| Idempotency | Δ ledger = 0 | Asserted in a test. |

### Secondary — human-touch rate

Finance teams care about time, not only rupees. The honest way to report it:

**Human-touch rate is fully measured and fully defensible:** *humans review
`N + M` of 500 items instead of 500.* Both numbers come out of the harness. It is
a ratio, not a time claim. **Lead with this.**

**An optional time estimate, if and only if it is disclosed properly:** time
yourself resolving 10 review-queue items by hand against the raw CSVs. Report as
*"author's own timing, n = 10, measured not estimated; not a substitute for a user
study, and biased fast because the author built the data."* A measured quantity
with a disclosed method and a disclosed bias is worth reporting. The disclosure is
what makes it worth more than the alternative.

**What must not be done:** citing an industry "analysts spend X hours on
reconciliation" statistic and multiplying it out. There is no observed baseline
here, and a fabricated one contaminates every real number in the submission.

---

## Feasibility risk — audited against ~2.5 build days

As of 2 September, with a 5 September deadline and submission day reserved, the
realistic budget is **~2.5 build days**. Total honest estimate for the full plan is
**32–42 hours**. At a hard 14 h/day that fits with **no slack**. Phases 02 and 03
together are ~60% of the build and both carry wide variance — that is the
structural risk, and everything else is comparatively predictable.

Nothing below is pre-cut. Each at-risk phase lists its trade-offs with costs, for
you to decide in the moment.

### Phase 01 — Foundations · LOW risk · 3–4 h
Well-understood code: canonical schema, integer-paise money type, tz-aware IST and
the named cutoff constant, versioned fee config, audit-log writer, run manifest.
No meaningful risk. Do not start with the matcher.

### Phase 02 — Generator and ground truth · **HIGHEST RISK** · 8–10 h
Three divergent sources, `ground_truth.json`, 8 hard-case types, ~40 adversarial
near-misses. Fiddly because every seeded case must be traceable in ground truth.
This is the phase people underestimate, and it caps everything downstream.

**If it runs over:**
- **(A) Cut adversarial instances from ~40 to ~15, keeping all 7 types.** Cost: a
  smaller denominator — "0 false matches in 15" carries less statistical weight
  than "in 40." Demonstrated capability is identical. *Preferred.*
- **(B) Cut hard-case types from 8 to 5** (drop rounding residual, reserve,
  holiday). Cost: loses exactly the timing-sophistication cases that domain-aware
  reviewers look for. *Least preferred.*
- **(C) Drop 500 records to 250, keeping every case type.** Cost: loses the "10× the
  stated bar" talking point; keeps all capability.

Order: A, then C, then B. Case *types* matter more than instance counts.

### Phase 03 — Deterministic core · **HIGH RISK** · 6–10 h
Tiers T0–T3, fee/GST/TDS/reserve decomposition, disposition routing, typed
exceptions. T3 subset-sum with all-solutions enumeration and the uniqueness test
is the hardest algorithmic piece in the build and has the widest variance.

**If it runs over:**
- **(A) Bound T3 at k ≤ 6 and a smaller pool instead of k ≤ 12.** Cost: more
  `SEARCH_BUDGET_EXCEEDED` exceptions — which is *not a failure*, it is the system
  correctly declining, and arguably a better story than a wider bound. Nearly
  free; take it early rather than late.
- **(B) T3 as review-queue-only, never auto-applied.** Already the design, so this
  costs nothing. Confirm it rather than "deciding" it.
- **(C) Drop T4 fuzzy narration.** Cost: real — removes one of the three model call
  sites and weakens the AI-judgment section.

### Phase 04 — Leakage rules · MEDIUM · 4–5 h
Ten detectors, each small once reconciled state exists.

**If it runs over:** the ten split into **structural** (missing settlement,
duplicate payout, duplicate capture, refund not reached, chargeback not
recredited, reserve) and **contract-dependent** (fee overcharge, GST, zero-MDR,
short settlement). Build all structural plus fee and GST; drop reserve and
short-settlement first. Cost: two fewer rows in the table, no credibility loss —
the structural set is the half that carries the argument.

### Phase 05 — Metrics harness · **DO NOT COMPROMISE** · 3–4 h
Deliberately lands *before* the model layer, so the deterministic core is proven
standalone and every AI addition is measured against a known baseline. Without
this you have a demo, not a submission. If time is short, cut from Phase 07, never
from here.

### Phase 06 — Model layer and refusal benchmark · MEDIUM · 5–6 h
Three call sites plus the LLM-as-matcher benchmark.

**If it runs over: do the benchmark first, then the call sites.** The benchmark is
the criterion-3 *evidence*; the call sites are the criterion-3 *implementation*.
The benchmark plus two call sites (narration, exception rationale) beats three
call sites and no benchmark. Drop settlement Q&A first — least load-bearing.

Re-run the full harness with the model layer on. If false-match rate moved at all,
the boundary is leaking; find it.

### Phase 07 — Surface · decide on merit, not on schedule
**This decision is deferred, and it is deferred on its own merits — not as a
time-saving measure.** The question is solely which surface best demonstrates the
work to someone watching for five minutes: a polished CLI report recorded in a
terminal reads as an ops tool, which is what this is; a finished dashboard makes
the exception queue and the arithmetic derivations easier to read at a glance.
Both are legitimate answers. Decide when Phase 06 is complete and the actual
output is in front of you, by looking at which one shows the engine's behaviour
more clearly. **Schedule pressure is not an input to this decision.** The one
ordering rule that does hold: if the choice is ever a prettier surface versus
harder seeded cases, the cases win.

### Phase 08 — Submission · **RESERVE 5–6 HOURS MINIMUM**
Freeze code the morning of the 5th. README, architecture note, video against a
frozen build — never a live one. The most common failure across this entire
buildathon will be people writing the README at 2 a.m. and shipping one bad take.

---

## The hardest question, and the honest answer

> **"You wrote the fee schedule. You generated data using that schedule. Then you
> wrote a detector that compares against that schedule. What did your system
> actually discover that you didn't put there?"**

**Concede the fee detectors completely and immediately.** For `FEE_OVERCHARGE` and
`GST_MISMATCH`, the answer is: nothing. Those rules verify an invariant I defined.
They demonstrate the arithmetic and the plumbing are correct. They do not
demonstrate discovery, and I would not present them as though they did.

**Then draw the real distinction.** The system's claim is not discovery — it is
**correct behaviour under ambiguity**, which is a different and more useful
property. Two behaviours were not put there by hand:

- Given a settlement where two distinct payment subsets both reconcile within
  tolerance, it declines to match. I did not label which cases were ambiguous; the
  uniqueness test found them.
- Given a narration the deterministic parser cannot resolve, the arithmetic
  rejects the model's proposed entity. I did not hand-label which proposals were
  wrong; the verification step caught them.

**Then name the experiment that would settle it.** Take one real bank statement and
one real gateway settlement report from any merchant. **Withhold the fee schedule
from the system** and change the task to: infer the effective rate per instrument
from observed settlements, then flag the deviations. That converts this from
verification into discovery, and it is the first thing I would build next — not
more detectors.

---

## README structure

Four sections, named exactly as the rubric names them. They published the
criteria; most applicants will still write a generic README.

- **Problem taste** — the pending tab that gets written off. Why leakage rather
  than match rate. The merchant's question, not the engineer's.
- **Build quality** — integer paise, determinism and idempotency assertions, typed
  exits, versioned config, quarantine over silent drops. Link the passing runs.
- **AI judgment** — three uses, four refusals, the benchmark table. Lead with the
  refusals.
- **Failure recovery** — link `INCIDENTS.md`, pull the best entry inline with its
  guard line.

Plus, near the top and before the metrics: **Who this is for and what it
replaces**, and **Honest scope** — both as written above.

---

## Unchanged from the original plan

The technical architecture stands. Review changed exactly two things, both for
honesty rather than capability:

1. **Headline reweighted toward structural detectors**; fee findings demoted to
   secondary and labelled contract-dependent; `ZERO_MDR_VIOLATION` excluded from
   aggregate scoring.
2. **"Confidence" reframed as an ordinal tier label**, not a calibrated
   probability, with calibration named as future work.

Everything else — the settlement identity, the six-tier cascade, subset-sum
uniqueness and the ambiguity refusal, the ten-class taxonomy, the seeded case
catalogue, integer paise, determinism, idempotency, the append-only audit log, the
build order, the video cue sheet, and the panel questions — carries forward from
the full plan unchanged.
