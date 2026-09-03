# Submission checklist and video cue sheet

**Status: not ready to record.** Two gates below are open. Recording against a
build with a known open issue is how a demo becomes a claim you have to walk back
in the panel.

## Gates before recording

| Gate | State |
|---|---|
| Report free of known honesty issues | **CLEAR** — INC-017 fixed, report regenerated |
| Bank row discrepancy explained | **CLEAR** — 43→41 traced to the refund-seeding fix at `5d7b3bc` |
| LLM-as-matcher benchmark | **OPEN** — no credentials; declared as a stated gap |
| Code frozen | **OPEN** — freeze before recording, record against the frozen build |

The benchmark does not block recording: it is declared honestly in both the README
and the report, and the video says so out loud rather than skipping past it.

---

## Video cue sheet — 5:00

Open on the number. No title card, no architecture tour, no "hi, I'm —". A
reviewer has a queue.

| Time | Beat | What is on screen |
|---|---|---|
| **0:00** | **The numbers, then why** | Numbers first, in this order, before any explanation: **"₹4,74,517 confirmed. A further ₹10,60,629 flagged but not confirmed. 22 records refused."** Then, and only then, one sentence of method: *"the split is there because two detectors measure below 1.00 precision, and I won't quote them as fact."* A reviewer needs the figure before the reason it is structured that way — methodology first sounds like hedging before you have earned the right to hedge. |
| **0:25** | The question | One sentence in a merchant's words: *"my settlement landed, my books balanced, and I still cannot tell you whether I was paid correctly."* Then why a match rate does not answer it. |
| **0:50** | The run | One command. 770 records, ~1,000 records/s. Cascade tiers resolving. End on the findings table **with the precision column visible**. |
| **1:45** | One finding, end to end | A fee overcharge. Show the arithmetic: expected against the versioned slab, actual, delta, schedule hash. A reviewer must be able to re-derive it from the frame. |
| **2:20** | **The refusal** | The ambiguity panel. Two candidate reconciliations side by side, differing only in which twin they exclude — highlighted. Say it: *"it found two equally valid answers, so it refused to pick one."* This is the shot the whole video exists for. |
| **3:00** | Honest limits | The precision column on `MISSING_SETTLEMENT`: 0.20. *"This detector is 20% precise and my headline says so. Its value is flagged, not confirmed."* Then `SHORT_SETTLEMENT`: detectable, not quantifiable, claims ₹0. |
| **3:30** | The exception list | 22 unresolved, each typed, each with a next action. Read it as a deliverable, not an apology. |
| **4:00** | The AI boundary | Three uses, four refusals. The adversarial provider: every metric identical, false-match rate still 0.0000. **Say the benchmark has not been run and why** — do not skip it. |
| **4:30** | One incident | INC-012 or INC-014. Symptom, root cause, fix, and the guard now in CI. Close there, not on a summary slide. |

**Rules for the recording**
- Record against a frozen commit. Never a live edit.
- Every number spoken must appear on screen from `harness/score.py` or the report.
- Do not say "found ₹15.3L". The gross is real but 69% of it is unconfirmed.
- If asked what the flagged bucket is: **99.8% of it is one detector** —
  `MISSING_SETTLEMENT` at 0.20 precision. Say that rather than implying two
  detectors are equally shaky; `RESERVE_NOT_RELEASED` is 0.18% of it.
- If something breaks mid-take, keep the take and narrate it — this project's
  entire argument is about handling failure honestly.

---

## Deliverables

| Item | Where |
|---|---|
| Repo | this directory |
| README, structured to the four criteria | `README.md` |
| Architecture note | `ARCHITECTURE.md` |
| Decision records | `DECISIONS.md` (ADR-001..004) |
| Incident log | `INCIDENTS.md` (17 incidents + PATTERN-01) |
| Scorecard | `harness/score.py` |
| Run report | `reports/run_report.html` |
| Ground-truth validator | `harness/validate_ground_truth.py` |
| Benchmark (unrun, frozen selection) | `harness/benchmark_llm_matcher.py` |

## The four criteria, and where each is answered

- **Problem taste** — README § Problem taste. The pending tab that gets written
  off; outcome metric over process metric.
- **Build quality** — README § Build quality. Eight CI-asserted properties,
  mutation-tested guards, ground truth validated before scoring.
- **AI judgment** — README § AI judgment. Three uses, four refusals, boundary
  tested adversarially in both directions. Benchmark declared unrun.
- **Failure recovery** — README § Failure recovery and `INCIDENTS.md`. INC-012
  (a defect in the primary metric), INC-014 (the engine right, the data wrong),
  INC-017 (confidence overstated — INC-010 recurring after being named), and
  PATTERN-01 (tests that passed without testing anything).

## Panel questions worth rehearsing

1. *"Your data is synthetic — why believe any of it?"* Concede, then redirect to
   what synthetic data does prove: deterministic, inspectable detection logic and a
   published generator. Name what it cannot establish before they do.
2. *"Your headline says 20% precision on your biggest detector."* Agree — that is
   why the headline splits confirmed from flagged. INC-017 is the story of catching
   exactly that, after having already named the same failure mode once.
3. *"Where does this break at 10M records?"* T3 is the superlinear stage, bounded
   at `d ≤ 6` with a meet-in-the-middle join. Windowing by counterparty and cycle,
   parallel per window. Never claim it scales as built.
4. *"Why not let an LLM do the matching?"* Non-determinism first, not accuracy —
   different books on re-run is disqualifying regardless of correctness. Then:
   the benchmark that would have measured it has not been run, and here is exactly
   why and what is frozen so it can be.
