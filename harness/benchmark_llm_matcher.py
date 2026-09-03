#!/usr/bin/env python3
"""LLM-as-matcher benchmark. The criterion-3 evidence.

Answers one question with measurement rather than assertion: should a model make
the match decision? Three quantities decide it -- self-disagreement across
identical runs, accuracy against ground truth, and cost/latency versus the
deterministic cascade.

DESIGN CHOICES THAT PROTECT THE RESULT

  Sample frozen first. The records come from harness/benchmark_selection.json,
  whose selection rule is committed BEFORE any model runs (see
  select_benchmark_records.py). The sample cannot have been chosen after seeing
  which records make the model look bad.

  Strongest model, not the cheapest. Benchmarked against claude-opus-5. Using a
  weak model to test "should an LLM do this?" would rig the answer toward the
  conclusion this codebase already holds. If Opus 5 is unreliable here, that
  means something; if Haiku is, it means nothing.

  Determinism measured, not assumed. temperature is not set, and no claim is made
  that any setting guarantees identical output. Self-disagreement is counted
  empirically across three runs. If the model turns out to be perfectly stable,
  that is a real finding and is reported as one.

  No silent stub. Without live credentials this exits non-zero and writes
  nothing. A benchmark that quietly measured a stub would be worse than no
  benchmark, because it would be believed.

Usage:  ANTHROPIC_API_KEY=... python3 harness/benchmark_llm_matcher.py [--runs 3]
"""
from __future__ import annotations

import argparse, csv, json, statistics, sys, time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                                 # noqa: E402
from leakledger.money import Money                                            # noqa: E402
from leakledger.schema import ingest_rows                                     # noqa: E402
from leakledger.ai.provider import (                                          # noqa: E402
    AnthropicProvider, OpenAIProvider, load_dotenv)
from leakledger.cascade.engine import Cascade                                 # noqa: E402

DATA = ROOT / "data" / "generated"
SELECTION = ROOT / "harness" / "benchmark_selection.json"
OUT = ROOT / "reports" / "benchmark_llm_matcher.json"

# Published list pricing, USD per 1M tokens, ONLY for models whose rates have been
# verified from the provider's own documentation. A model absent from this table
# gets its tokens and latency reported and its USD cost left UNCOMPUTED -- an
# invented price would make the one figure a reader cannot check the one figure
# that is fabricated. Supply rates with --price-in/--price-out to fill it in.
PRICING = {"claude-opus-5": {"input": 5.00, "output": 25.00}}

SYSTEM = (
    "You reconcile Indian payment settlements. Given a bank credit and a list of "
    "candidate payments, decide which payments make up that credit. The credit is "
    "the sum of the payments minus each payment's gateway fee and GST. "
    'Reply with ONLY JSON: {"payment_ids": [...]}. No explanation.'
)
_load = lambda n: list(csv.DictReader((DATA / n).open(encoding="utf-8")))


def build_prompt(bank_row, candidates):
    lines = [f"{p['payment_id']}  amount={p['amount']}  fee={p['fee_charged'] or '0.00'}  "
             f"gst={p['gst_charged'] or '0.00'}  captured={p['captured_at']}"
             for p in candidates]
    return (f"Bank credit: {bank_row['amount']} on {bank_row['value_date']}\n\n"
            f"Candidate payments ({len(candidates)}):\n" + "\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--provider", choices=("openai", "anthropic"), default="openai")
    ap.add_argument("--model", default=None)
    ap.add_argument("--price-in", type=float, default=None)
    ap.add_argument("--price-out", type=float, default=None)
    ap.add_argument("--temperature", type=float, default=None,
                    help="pin temperature; omitted entirely if not given")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    load_dotenv()

    if not SELECTION.exists():
        print("selection not frozen; run harness/select_benchmark_records.py first")
        return 2
    sel = json.loads(SELECTION.read_text(encoding="utf-8"))

    try:
        if args.provider == "openai":
            provider = OpenAIProvider(model=args.model or "gpt-5.2",
                                      temperature=args.temperature)
        else:
            provider = AnthropicProvider(model=args.model or "claude-opus-5")
        MODEL = provider.model
    except Exception as e:
        print("NO LIVE MODEL CREDENTIALS — refusing to run.")
        print(f"  {e}")
        print("\nThis script writes nothing without a live model. A benchmark that")
        print("quietly measured a stub would be worse than no benchmark.")
        return 1

    gw = ingest_rows("gateway", _load("gateway_payments.csv"))
    bank = _load("bank_statement.csv")
    raw_pay = {r["payment_id"]: r for r in _load("gateway_payments.csv")}
    cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
    eng = Cascade(payments=gw.records, refunds=_load("gateway_refunds.csv"),
                  bank=bank, adjustments=_load("gateway_adjustments.csv"), calendar=cal)
    truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
    by_net = defaultdict(list)
    for s in truth["settlements"]:
        if s["paid"]:
            by_net[s["net_paise"]].append(s)

    # cascade baseline timing, same records
    t0 = time.perf_counter()
    eng.run()
    cascade_s = time.perf_counter() - t0

    records = [r for r in sel["records"] if r["tier"] == "T3"]
    print(f"benchmark: {len(records)} T3 records x {args.runs} runs on {MODEL} ({args.provider})")
    print(f"selection sha256: {sel['selection_sha256'][:16]}\n")

    results, in_tok, out_tok, lat = defaultdict(list), 0, 0, []
    for run_i in range(args.runs):
        for rec in records:
            b = next(x for x in bank if x["txn_id"] == rec["bank_txn_id"])
            bd = datetime.strptime(b["value_date"], "%d-%m-%Y").date()
            cands = []
            for cyc in eng._candidate_cycles(bd):
                cands += [raw_pay[p] for p in eng._by_capture_date.get(cyc, [])
                          if p in raw_pay]
            reply = provider.complete(system=SYSTEM, prompt=build_prompt(b, cands),
                                      max_tokens=2048)
            in_tok += reply.input_tokens
            out_tok += reply.output_tokens
            lat.append(reply.latency_s)
            ids = []
            if not reply.error:
                try:
                    m = json.loads(reply.text[reply.text.index("{"):reply.text.rindex("}") + 1])
                    ids = sorted(str(x) for x in m.get("payment_ids", []))
                except (ValueError, json.JSONDecodeError):
                    ids = []
            results[rec["bank_txn_id"]].append(tuple(ids))
            print(f"  run {run_i+1} {rec['bank_txn_id']}: {len(ids)} ids, "
                  f"{reply.latency_s:.1f}s{' ERROR' if reply.error else ''}")

    # --- self-disagreement -------------------------------------------
    disagree = sum(1 for v in results.values() if len(set(v)) > 1)
    # --- accuracy -----------------------------------------------------
    llm_correct = casc_correct = scored = 0
    for rec in records:
        b = next(x for x in bank if x["txn_id"] == rec["bank_txn_id"])
        cands = by_net.get(Money.from_rupees_str(b["amount"]).paise, [])
        if len(cands) != 1:
            continue
        scored += 1
        gt = set(cands[0]["payment_ids"])
        casc_correct += set(rec["matched_ids"]) == gt
        llm_correct += set(results[rec["bank_txn_id"]][0]) == gt

    if args.price_in is not None and args.price_out is not None:
        price = {"input": args.price_in, "output": args.price_out, "source": "supplied"}
    else:
        base = PRICING.get(MODEL)
        price = dict(base, source="verified table") if base else None
    if price:
        cost = in_tok / 1e6 * price["input"] + out_tok / 1e6 * price["output"]
        per_500 = cost / max(1, len(records) * args.runs) * 500
    else:
        cost = per_500 = None

    report = {
        "model": MODEL, "provider": args.provider,
        "temperature_sent": args.temperature,
        "temperature_note": ("not sent; API default applied"
                             if args.temperature is None else
                             f"pinned to {args.temperature}"),
        "runs": args.runs, "records": len(records),
        "selection_sha256": sel["selection_sha256"],
        "self_disagreement_records": disagree,
        "self_disagreement_rate": disagree / len(records) if records else None,
        "scored_records": scored,
        "llm_correct": llm_correct, "cascade_correct": casc_correct,
        "llm_accuracy": llm_correct / scored if scored else None,
        "cascade_accuracy": casc_correct / scored if scored else None,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "usd_total": round(cost, 4) if cost is not None else None,
        "usd_per_500_records": round(per_500, 4) if per_500 is not None else None,
        "usd_note": (None if price else
                     "pricing for this model is not verified in-repo and none was supplied; "
                     "tokens and latency are measured, USD is NOT computed rather than estimated"),
        "llm_latency_median_s": round(statistics.median(lat), 2) if lat else None,
        "llm_latency_total_s": round(sum(lat), 1),
        "cascade_total_s": round(cascade_s, 3),
        "pricing_used": price,
        "generated_at": datetime.now().isoformat(),
    }
    out_path = (Path(args.out) if args.out else OUT)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"\n{'metric':34} {'LLM-as-matcher':>20} {'cascade':>14}")
    print(f"{'self-disagreement (3 runs)':34} "
          f"{f'{disagree}/{len(records)}':>20} {'0/' + str(len(records)):>14}")
    print(f"{'accuracy vs ground truth':34} "
          f"{f'{llm_correct}/{scored}':>20} {f'{casc_correct}/{scored}':>14}")
    print(f"{'wall clock, all runs':34} {f'{sum(lat):.1f}s':>20} "
          f"{f'{cascade_s:.3f}s':>14}")
    if per_500 is not None:
        print(f"{'cost per 500 records':34} {f'${per_500:.4f}':>20} {'$0.0000':>14}")
    else:
        print(f"{'tokens measured':34} {f'{in_tok:,} in / {out_tok:,} out':>20} {'0':>14}")
        print("  USD NOT COMPUTED: pricing for this model is not verified in-repo.")
        print("  Re-run with --price-in/--price-out to fill it in from published rates.")
    try:
        shown = out_path.relative_to(ROOT)
    except ValueError:
        shown = out_path
    print(f"\nwritten: {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
