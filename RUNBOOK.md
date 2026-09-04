# Runbook — how to actually run this

Everything here runs on your machine with `python3` and nothing else installed.
No `pip install`, no `npm`. Copy-paste each block.

```bash
cd ~/Desktop/Razorpay
```

---

## 1. The dashboard (what you show people)

```bash
python3 server.py
```

Then open **http://localhost:8000**. Press **▶ Run reconciliation** — the pipeline
executes live and the page fills in.

`Ctrl-C` in the terminal stops the server.

**What is actually happening when you press that button:** the browser POSTs to
`/api/run`; the server calls `harness/payload.py`; that runs the matching cascade
and the ten detectors over the CSVs in `data/generated/`; the result comes back as
JSON and the page renders it. **The server computes nothing of its own** — it reads
the same code the command-line scorecard reads, so the two can never disagree.

Run it on a different port if 8000 is taken: `python3 server.py 8080`

**If you see `OSError: [Errno 48] Address already in use`** — a server is already
running on that port (often one you started earlier and left open). Either use it,
or free the port:

```bash
lsof -ti:8000 | xargs kill      # stop whatever is on 8000
python3 server.py               # then start fresh
```

---

## 2. The numbers, in the terminal

```bash
python3 harness/score.py
```

The full scorecard: false-match rate, disposition, per-class precision and recall,
value split, throughput, exception queue, ledger balance. **Every number quoted in
the README and the dashboard comes from here.**

---

## 3. The static report (a file you can email)

```bash
python3 harness/report.py
open reports/run_report.html
```

Same figures as the dashboard, as a self-contained HTML file. Useful when you want
to send something rather than demo something.

---

## 4. Prove the data is not rigged

```bash
python3 harness/validate_ground_truth.py
```

672 checks on the *test data itself*, run before anything is scored against it:
every settlement's stated total equals the sum of its parts, every reference
resolves, every adversarial case is actually reachable.

This exists because of INC-014 — a settlement was once seeded whose own numbers
contradicted each other, and the engine was correctly refusing it while looking
broken. **You cannot catch that by testing the engine harder**, because the engine
is being graded by the thing that is wrong.

---

## 5. Prove the AI cannot touch the money

```bash
python3 harness/model_layer_check.py
```

Runs the whole pipeline twice — once normally, once with a deliberately **wrong**
model wired in that returns confident, well-formed, incorrect answers. Every number
must come out identical. It prints `BOUNDARY HOLDS` or `BOUNDARY LEAKS`.

---

## 6. Regenerate the test data

```bash
python3 data/generate.py
```

Rebuilds all five CSVs plus `ground_truth.json` from a fixed seed. Run it twice and
the files are byte-for-byte identical — that is asserted in the test suite.

---

## 7. The whole test suite

```bash
python3 -m unittest discover -s tests -q
```

Takes ~20-35 seconds. Includes determinism (5 runs, 1 hash), idempotence
(re-applying changes nothing), double-entry balance, and the false-match rate
pinned at a ceiling of 0.

**It is slower on this machine than it should be**, because the project sits on an
iCloud-synced Desktop and several tests re-read the whole dataset. Nothing is
broken — if it looks stuck, give it 40 seconds before assuming otherwise. Moving
the folder out of iCloud makes it about four times faster:

```bash
mkdir -p ~/dev && mv ~/Desktop/Razorpay ~/dev/Leak-Ledger && cd ~/dev/Leak-Ledger
```

---

## 8. The LLM benchmark (costs money — needs a key)

```bash
python3 harness/benchmark_llm_matcher.py --runs 3 --provider openai
```

Reads `OPENAI_API_KEY` from `.env`. Already run; results are in
`reports/benchmark_llm_matcher.json`. **You do not need to run this again** unless
you want fresh numbers. It refuses to run without a key rather than silently
faking one.

---

## If someone asks "what is this actually doing?"

Say it in this order:

1. **Three files arrive that disagree.** A payment gateway export (individual
   payments), a bank statement (only lump-sum payouts, no payment detail), and the
   merchant's own invoice register. Nobody's IDs match anybody else's.
2. **It works out which payments make up each bank payout.** A payout is the day's
   payments minus fees, tax, refunds, chargebacks and reserve — so no bank line
   ever equals a payment amount. That is the hard part.
3. **When two answers are equally valid, it refuses.** It does not guess. That is
   the panel on the dashboard showing two candidates side by side.
4. **Then it looks for money that went missing** — payouts that never arrived,
   payouts paid twice, refunds that never reached the customer, disputes won but
   never credited back, fees charged above contract.
5. **It says how much of that it can actually prove.** ₹4.7L confirmed,
   ₹10.6L flagged but not confirmed — because one detector is only 20% precise and
   the headline says so instead of hiding it.

## What to click during the demo

| Order | Where | Why |
|---|---|---|
| 1 | **▶ Run reconciliation** | It executes live. 645 records in under a second. |
| 2 | The four cards | False-match rate 0.0000 first — that is the primary metric. |
| 3 | **A refusal panel** | The strongest thing here. Two reconciliations, differing by one payment id, and the engine declined to pick. |
| 4 | A findings row | Expands to the arithmetic. A reviewer can re-derive it on screen. |
| 5 | The `MISSING_SETTLEMENT` row | Precision 0.20, stated openly. Say: *"this detector is 20% precise and my headline says so."* |
| 6 | The benchmark table | 9 of 13 self-disagreement — and 7 of 13 even with temperature pinned to 0. |

## If something breaks mid-demo

Say what happened and keep going. This project's whole argument is about handling
failure honestly — a crash you narrate is on-message, and 19 logged incidents back
you up.
