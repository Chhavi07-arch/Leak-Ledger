# Leak Ledger

**Razorpay AI Buildathon — Track 04, AI Finance Controller**

A reconciliation engine that reports what the books are *hiding*, not what they
matched. Deterministic where money is decided, AI only where language is
ambiguous, honest about everything it could not resolve.

```
Across 538 payments and 41 bank rows, Leak Ledger CONFIRMED Rs 4,74,517.27 of
leakage — 2 duplicate payouts, 3 refunds that never reached the customer, 3
duplicate captures, 2 disputes won but never re-credited — every instance verified
against ground truth. It FLAGGED a further Rs 10,60,629.55 that is NOT confirmed:
those detectors report instances ground truth does not support. It refused to
match 22 records, each with a stated cause. False-match rate: 0.0000.
```

The split is the point. The gross figure is Rs 15,35,146.82, but 69% of it comes
from two classes whose measured precision is below 1.00 — `MISSING_SETTLEMENT` at
0.20 and `RESERVE_NOT_RELEASED` at 0.67. Quoting the gross alone would overstate
confidence in exactly the way this build argues against, so the headline never
does (INC-017).

**And the flagged bucket is not evenly unreliable:** 99.8% of it is
`MISSING_SETTLEMENT` alone; `RESERVE_NOT_RELEASED` is 0.2%. That is stated as a
measured share rather than as a reliability grade, because where one would draw
the line between "moderate" and "low" confidence is a judgement nothing here
measures — and an unmeasured label is the thing this project spends its whole
argument refusing.

Every figure above is produced by `harness/score.py`. None is typed by hand, and
a test asserts this README agrees with the harness.

---

## Who this is for, and what it replaces

**A finance-operations associate** at a mid-size merchant processing 5,000–50,000
transactions a month. Not an engineer; lives in a spreadsheet.

Today, on settlement day, they download the gateway report and the bank
statement, paste both into Excel, sort by amount and date, and eyeball the
difference. When the totals don't tie they work backwards by hand. Whatever never
resolves goes onto a *pending* tab that grows month over month and is written off
at quarter end as "unreconciled differences". **That write-off is the leakage, and
nobody ever finds out what it was.**

Leak Ledger replaces the spreadsheet and the eyeballing. It pairs the records
itself, recomputes every fee and tax line against a versioned contract, and turns
the pending tab from an untyped lump that grows into a typed queue that shrinks —
every item carrying its own arithmetic and a specific next action.

| When | Time | What they open | What they do |
|---|---|---|---|
| Daily | ~5 min | Run summary | Three numbers. If review and exceptions are empty, close the tab. |
| Settlement day | 30–45 min | Review queue | Approve or reject each proposed match. Rejections become labelled data. |
| Monthly | ~1 hr | Findings report | Goes to the controller — itemised by cause, not a lump sum. |

### Why an auto-applied match can go unreviewed

Not because a confidence score is high. Tier labels here are **ordinal ranks
assigned by rule, not calibrated probabilities**, and nothing in this build
establishes otherwise. Three structural properties justify it instead:

1. **Tier 1 is a lookup, not an inference.** An exact reference with a
   corroborating amount contains no judgement.
2. **Tier 2 auto-applies only under a uniqueness constraint** — the auto-apply
   population is defined by *absence of alternatives*, not by a score.
3. **The safety net is reversibility, not accuracy.** Every apply is written to an
   append-only log with full evidence, and apply is idempotent, so a corrected
   re-run reverses cleanly. Skipping review is acceptable because being wrong is
   *detectable and recoverable*.

**Failure cost when it is wrong and nobody checks:** a payment is marked settled
that never settled, leaves the queue, and the merchant silently absorbs it. That
is precisely the loss this tool exists to prevent — a false match is the tool
committing the original sin it was built to catch.

---

## Honest scope

**What it is:** a working reconciliation and leakage-detection engine for a single
merchant with a known contract, on a 770-record synthetic batch across three
divergent sources, with published ground truth.

**What it is not:** real source connectors (it reads CSVs); contract ingestion
(the fee schedule is hand-authored config, and inferring effective rates from
observed settlements is a harder problem this does not attempt); a multi-user
review workflow; continuous operation.

### The circularity, disclosed rather than hidden

`FEE_OVERCHARGE`, `GST_MISMATCH` and `ZERO_MDR_VIOLATION` are **verification, not
discovery.** The fee schedule is authored here, the data generated from it, and
the detector compares against it. They prove the arithmetic and the plumbing are
correct; they do not find anything nobody put there.

So the headline leads with **structural** findings — missing settlement, duplicate
payout, duplicate capture, refund not reached, chargeback not re-credited — which
detect loss *without needing to know a contract rate*. `ZERO_MDR_VIOLATION` is
reported as a rule check and **excluded from aggregate scoring**: its precision is
1.0 by construction, and counting it would inflate the scorecard with a free win.

---

## Problem taste

Reconciliation is usually framed as a matching problem and scored with a match
rate. A match rate is a *process* metric: it tells a merchant their books
balanced, which is not what they want to know. They want to know where the money
went.

So this reports an **outcome** metric — rupees of leakage, itemised by cause — and
treats the exception queue as a deliverable rather than a residue. The design
consequence is that **an exception is never counted as a leak.** An early version
of `MISSING_SETTLEMENT` flagged every payment the cascade had not positively
matched, which swept in every payout it had *refused* and inflated the headline
from ₹8.4L to ₹25.3L. Reporting ignorance as loss is worse than reporting
nothing, because the number is confident and wrong (INC-010).

---

## Build quality

```
$ python3 -m unittest discover -s tests -q
Ran 174 tests ... OK
```

| Guarantee | How it is enforced |
|---|---|
| Money is integer paise | Floats rejected at construction; half-up away from zero, never Python's banker's rounding |
| Timestamps tz-aware IST | Naive datetimes rejected; `SETTLEMENT_CUTOFF_IST` a named constant |
| Determinism | 5 runs → 1 hash, asserted in CI |
| Idempotence | Δ ledger = 0 on second apply, asserted in CI |
| Double entry | Trial balance = ₹0.00, asserted in CI |
| Nothing dropped | Rows in == records + quarantined, always; every exit typed |
| Provenance | Fee schedule content-hashed into every run manifest and every finding's derivation |
| **False-match rate** | **Pinned in CI at a ceiling of 0.0**, with a floor on scoreable matches so a change that stops matching cannot pass silently |

**The guards are mutation-tested.** Disabling the primary-cycle fix reproduces a
false-match rate of exactly 0.0667 (1 wrong of 15); breaking idempotence fails the
delta test. A passing test is not evidence until it has been observed to fail when
the behaviour is absent.

**Ground truth is validated before anything is scored against it.** 672
consistency checks — settlement identity closes exactly, references resolve, every
adversarial case is reachable. See *Failure recovery* for why.

### Measured performance

| | |
|---|---|
| False-match rate | **0.0000** (13 of 13 correct) |
| Adversarial population (UTR reused, altered amount) | **0.0000** — 5 of 5 refused |
| Auto-applied / review / exception | 14.6% / 31.7% / 53.7% |
| Human-touch rate | 35 of 41 items (85.4%) — a ratio, not a time claim |
| Throughput | 770 records end to end, ~1,000 records/s |
| Ledger | 39 entries, trial balance ₹0.00 |

Per-class precision and recall are in `harness/score.py` output and
`reports/run_report.html`, shown **beside** each class's claimed value so a reader
never has to infer how much of a figure is trustworthy. **Never aggregated** — an
aggregate hides the classes that do not work.

| class | TP | FP | FN | precision | claimed | verified |
|---|---|---|---|---|---|---|
| CHARGEBACK_NOT_RECREDITED | 2 | 0 | 0 | 1.00 | ₹33,936.99 | ₹33,936.99 |
| DUPLICATE_CAPTURE | 3 | 0 | 0 | 1.00 | ₹53,251.49 | ₹53,251.49 |
| DUPLICATE_PAYOUT | 2 | 0 | 0 | 1.00 | ₹3,21,241.23 | ₹3,21,241.23 |
| REFUND_NOT_REACHED | 3 | 0 | 0 | 1.00 | ₹65,833.49 | ₹65,833.49 |
| FEE_OVERCHARGE | 11 | 0 | 0 | 1.00 | ₹231.01 | ₹231.01 |
| GST_MISMATCH | 6 | 0 | 0 | 1.00 | ₹10.30 | ₹10.30 |
| **RESERVE_NOT_RELEASED** | 2 | **1** | 0 | **0.67** | ₹1,858.23 | ₹1,842.91 |
| **MISSING_SETTLEMENT** | 1 | **4** | 2 | **0.20** | ₹10,58,771.32 | ₹3,72,917.77 |
| SHORT_SETTLEMENT | — | — | 2 | n/a | ₹0.00 | — |

**"Claimed" sums every instance the engine reported, not only the verified ones**
— because that is what the tool actually claims when it runs. A deployed engine
has no ground truth and cannot filter its output down to the instances that
happen to be right; reporting only verified value would flatter the tool using
knowledge it does not possess at run time. The verified column sits beside it, and
any class with FP > 0 is excluded from the confirmed headline.

`MISSING_SETTLEMENT`'s precision is capped by an information limit proven three
ways (INC-015). `SHORT_SETTLEMENT` is detectable but **not quantifiable** and
therefore claims no rupee value at all (INC-013). Both are held to the same
disclosure standard: state what is measured, claim nothing beyond it.

---

## AI judgment

**Three places a model is used. Four places it is refused.**

| Used | Constraint |
|---|---|
| Bank narration → candidate entities | Proposes only. Accepted only if the named reference exists **and** the amount agrees exactly. **Confidence is never consulted.** |
| Exception rationale | Discarded if it introduces a figure absent from the evidence; the deterministic string is shown instead. |
| Settlement Q&A | Read-only. No write path exists — enforced by the function signature, not by convention. |

**Refused:** the match decision (non-deterministic — re-running would produce
different books, which is disqualifying in finance regardless of accuracy); any
arithmetic; leakage classification (must be a rule a human can re-derive, not a
model output a human must trust); any write to the ledger.

**The boundary is tested adversarially, not hopefully.** An `AdversarialProvider`
returns confidently wrong, well-formed answers — high-confidence references that
exist but belong elsewhere, references that do not exist at all. With it wired in,
**every metric is identical and false-match rate stays 0.0000**. A boundary that
depends on the model behaving is not a boundary.

And it is tested in both directions, because a gate that rejects everything holds
trivially: a **correct** proposal at confidence **0.30** is accepted; a **wrong**
one at confidence **0.99** is rejected.

### The benchmark — why a model does not make the match decision

Measured, not asserted. The same 13 settlement records the deterministic cascade
resolves, put through **`gpt-5.2`** three times.

| | `gpt-5.2` | deterministic cascade |
|---|---|---|
| **self-disagreement across 3 identical runs** | **9 / 13 (69%)** | 0 / 13 |
| accuracy vs ground truth | 1 / 13 (8%) | 13 / 13 (100%) |
| wall clock, all runs | 115.8 s | 0.626 s |
| tokens consumed | 88,656 in / 6,497 out | 0 |

**Temperature — configured setting vs observed determinism.** The headline run sent
**no temperature parameter**, so the API default applied. Because `temperature = 0`
is often assumed to guarantee identical output, a second arm pinned it explicitly
on the same frozen selection:

| run | temperature sent | self-disagreement | accuracy |
|---|---|---|---|
| default | none sent (API default) | 9 / 13 | 1 / 13 |
| pinned | `temperature = 0` | **7 / 13** | 1 / 13 |

**Pinning temperature to 0 did not produce determinism.** It constrains sampling;
it is not a guarantee of identical output. That was measured rather than assumed.

### Why this result was expected — and why saying so matters

The task put to the model was **exact subset-sum over candidate pools of 16–87
payments (median 61)**, in free text: choose the subset whose amounts, net of each payment's
fee and GST, sum precisely to a given credit. **This is a task class language
models are structurally weak at regardless of model strength** — it needs
exhaustive combinatorial search with exact integer arithmetic and a uniqueness
check, not judgement, reading or inference. No amount of capability turns a
reasoning system into a search algorithm.

So the low accuracy is **not** the claim "gpt-5.2 is bad". It is the claim that
*this task shape does not suit an LLM at all* — which is precisely why
deterministic search owns the match decision, and why the model is confined to the
three places where the work genuinely is linguistic.

A benchmark whose result was foreseeable, reported without saying so, reads as a
task chosen because it was guaranteed to fail. The honest version: the outcome was
predicted from the task shape, the measurement confirms it, and **the
self-disagreement figure — not the accuracy figure — is what disqualifies the
approach**, because a system that answers differently on identical input cannot
keep books however often it is right.

**The exact prompt is committed** in `harness/benchmark_llm_matcher.py` and
reproduced verbatim in `reports/run_report.html`, so the framing can be checked.
The model received the same information the cascade uses — the credit, and every
candidate payment with its amount, fee and GST — with the settlement identity
stated for it rather than left to be inferred.

**Non-determinism is the finding; accuracy merely confirms it.** A system that
produces different books on identical re-runs is disqualified in finance even when
it happens to be right, and this one disagreed with itself on 9 of 13 records at
the same settings. That is the whole argument for keeping the model out of the
match decision, and it is now a measurement rather than an opinion.

**The model was chosen so the result cannot be dismissed.** Two objections had to
be closed off. A weak or throttled model would have supported only the narrower
claim that *that* model should not match — the classic way to build a benchmark
that confirms what you already believe. And this project was written using Claude
Code, so benchmarking against Anthropic's own model would have invited the obvious
"you tested the friendly vendor". `gpt-5.2` is a frontier model from a *different*
vendor, which closes both.

**The comparison is like for like.** Both sides searched the same candidate pools
(min 16, median 61, max 87 payments) and were scored by the same comparator — under which
the cascade scores 13/13, so a scoring defect would have depressed both sides
equally.

**Selection frozen before any model ran** (`harness/benchmark_selection.json`,
sha `f6abb21e6c760c`), so it provably predates the result. Its rule is
*cascade-resolved bank credits, ascending by txn_id*; the population is 19 of an
intended 50, and the shortfall is **reported, not padded** with refused records.

**Stated gap: USD cost is not computed.** Published per-token rates for this model
are not verified in-repo, and an invented price would make the one figure a reader
cannot check the one figure that is fabricated. Token counts and latency above are
measured from the API responses; `--price-in`/`--price-out` fills in the cost from
published rates.

---

## Failure recovery

`INCIDENTS.md` carries 19 incidents logged as they happened, and one named
pattern. Three are worth reading first.

**INC-012 — a defect in the primary metric itself.** The engine matched a bank
credit to a set of payments with **zero overlap** against the truth. False-match
rate 6.7%, in the metric the whole submission is argued on, while every individual
step looked correct — the search really did find an exact reconciliation, of the
wrong thing. The obvious fix (ordering candidates by posting lag) was **measured
and rejected**: when the correct answer is impossible, some wrong answer always
beats no answer. The real fix generalised a principle already established
elsewhere — *refuse rather than reach further when the near explanation fails.*
Result: **1 → 0**, with the cost stated rather than hidden.

**INC-014 — the engine was right, the data was wrong.** A regression after INC-012
looked like over-refusal. It was not: a settlement's stated net contradicted its
own components, because a withheld chargeback re-credit had been seeded *after*
payout nets were computed. **This is the failure mode that cannot be caught by
testing the engine harder** — the engine is graded by the very artefact that is
broken, so a self-contradictory fixture silently penalises a correct
implementation. The only defence is to test the data's internal consistency as a
first-class artefact, which `harness/validate_ground_truth.py` now does.

**PATTERN-01 — tests that passed without testing anything.** Four times, in four
phases, a test or seeded case existed, was counted, looked like coverage, and
exercised nothing: two GST policies asserted to "possibly differ" without ever
checking they do; six ambiguity traps seeded so both twins landed in the same
payout, making the refusal unreachable; leaks that silently cancelled adversarial
cases; a detector keyed on a reason code the engine never emits. Every one left
the suite green. The rule adopted afterwards: **a test or seeded case is not
evidence until it has been observed to fail when the behaviour is absent, or to
fire on real data.** `tests/test_all_detectors_fire.py` enforces it permanently.

---

## Run it

```bash
python3 server.py                             # dashboard at http://localhost:8000
python3 data/generate.py                      # regenerate the batch (seeded, deterministic)
python3 harness/validate_ground_truth.py      # 672 consistency checks — run before scoring
python3 harness/score.py                      # the scorecard
python3 harness/report.py                     # reports/run_report.html
python3 harness/model_layer_check.py          # boundary gate, adversarial provider
python3 -m unittest discover -s tests -q      # 174 tests
```

No dependencies beyond the standard library — including the dashboard server,
which uses `http.server` rather than a framework. Zero-dep was a deliberate
choice: "does it run" should be unconditional. **`RUNBOOK.md` has every command
and what each one actually does.**

The dashboard renders `harness/payload.py`, the same code the CLI scorecard reads.
It recomputes nothing of its own, so the two surfaces cannot disagree about a
number — asserted in `tests/test_dashboard.py`.

## Layout

```
config/     fee schedule (versioned, content-hashed) and holiday calendar
data/       generator + the batch it produces, with ground_truth.json
src/leakledger/
  money.py        integer paise; floats rejected
  clock.py        IST, settlement cutoff, business calendar
  schema.py       canonical records, typed quarantine
  feeschedule.py  slabs, GST, and the derivation of every fee
  cascade/        T0–T5 matching; subsetsum.py is the deviation search
  leakage/        ten typed detectors
  ledger/         double-entry, idempotent apply
  ai/             the only three model call sites, behind one interface
server.py   local dashboard server (stdlib only, no framework)
web/        the dashboard page it serves
harness/    scorecard, ground-truth validator, report, benchmark, shared payload
tests/      174 tests
DECISIONS.md  ADR-001..004 — choices a reader could reasonably have made differently
INCIDENTS.md  what broke, what it cost, and the guard that stops it recurring
```
