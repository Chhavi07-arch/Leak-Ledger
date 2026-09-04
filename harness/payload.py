"""One function that produces everything a surface needs, as plain JSON.

Single source of truth. The CLI scorecard, the static HTML report and the live
dashboard all read from here, so none of them can disagree with another about a
number. A dashboard that recomputed its own figures would be a second source of
truth, and the first time it drifted from the scorecard the whole submission's
credibility would go with it.
"""
from __future__ import annotations

import csv, json, sys, time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "harness"))
from leakledger.clock import BusinessCalendar                                 # noqa: E402
from leakledger.feeschedule import FeeSchedule                                # noqa: E402
from leakledger.ledger import Ledger, apply_run                               # noqa: E402
from leakledger.money import Money                                           # noqa: E402
from leakledger.schema import ingest_rows                                     # noqa: E402
from leakledger.cascade.engine import (                                       # noqa: E402
    AUTO_APPLY, EXCEPTION, REVIEW, Cascade, covered_cycles_by_matching)
from leakledger.leakage import detectors                                      # noqa: E402
from leakledger.leakage.findings import (                                     # noqa: E402
    CONTRACT_DEPENDENT, EXCEPTION_SIGNAL, RULE_CHECK, STRUCTURAL)
from scoring import headline_split, score_all                                 # noqa: E402

DATA = ROOT / "data" / "generated"
AS_OF = date(2026, 7, 31)
def _load(n):
    """Read a generated CSV. Uses a context manager so the handle is closed --
    the lambda this replaced leaked one per call and filled test runs with
    ResourceWarnings."""
    with (DATA / n).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_payload() -> dict:
    t_start = time.perf_counter()
    fs = FeeSchedule.from_file(ROOT / "config" / "fee_schedule.v1.json")
    cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
    gw = ingest_rows("gateway", _load("gateway_payments.csv"))
    refunds, adj = _load("gateway_refunds.csv"), _load("gateway_adjustments.csv")
    bank = _load("bank_statement.csv")

    t0 = time.perf_counter()
    eng = Cascade(payments=gw.records, refunds=refunds, bank=bank,
                  adjustments=adj, calendar=cal)
    casc = eng.run()
    t1 = time.perf_counter()
    cov = covered_cycles_by_matching(eng, bank)
    found = detectors.run_all(fs=fs, payments=gw.records, refunds=refunds, adjustments=adj,
                              bank_rows=bank, cascade_result=casc, calendar=cal,
                              as_of=AS_OF, covered_cycles=cov)
    t2 = time.perf_counter()

    truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
    scores = score_all(found, truth, bank)
    confirmed, flagged, flagged_cls = headline_split(found, scores)

    # ---- false-match rate, the primary metric ----
    by_net = defaultdict(list)
    for s in truth["settlements"]:
        if s["paid"]:
            by_net[s["net_paise"]].append(s)
    correct = wrong = 0
    for m in casc.matches:
        if m.tier != "T3" or m.disposition not in (AUTO_APPLY, REVIEW):
            continue
        b = next(x for x in bank if x["txn_id"] == m.bank_txn_id)
        c = by_net.get(Money.from_rupees_str(b["amount"]).paise, [])
        if len(c) != 1:
            continue
        if set(c[0]["payment_ids"]) == set(m.matched_ids):
            correct += 1
        else:
            wrong += 1
    adv = [m for m in casc.matches if m.bank_txn_id.endswith("X")]

    led = Ledger()
    apply_run(led, run_id="dash", cascade_result=casc, findings=found, payments=gw.records)

    # concentration + median, so the headline can never be quoted without its shape
    vals = sorted((f.value.paise for f in found.findings if f.value.paise), reverse=True)
    import statistics as _st
    concentration = {
        "top3_paise": sum(vals[:3]) if vals else 0,
        "top3_pct": round(100 * sum(vals[:3]) / sum(vals), 1) if vals else 0,
        "median_paise": int(_st.median(vals)) if vals else 0,
        "count": len(vals),
    }

    n = len(casc.matches)
    auto, rev, exc = (len(casc.by_disposition(d)) for d in (AUTO_APPLY, REVIEW, EXCEPTION))
    seeded = defaultdict(set)
    for l in truth["seeded_leaks"]:
        seeded[l["class"]].add(l["entity_id"])

    cat_label = {STRUCTURAL: "structural", CONTRACT_DEPENDENT: "contract-dependent",
                 RULE_CHECK: "rule-check", EXCEPTION_SIGNAL: "exception-signal"}

    findings = []
    for cls, fl in sorted(found.by_class().items()):
        s = scores.get(cls)
        findings.append({
            "leak_class": cls,
            "category": cat_label[fl[0].category],
            "found": len(fl),
            "seeded": len(seeded.get(cls, set())),
            "tp": s.tp if s and s.scoreable else None,
            "fp": s.fp if s and s.scoreable else None,
            "fn": s.fn if s and s.scoreable else None,
            "precision": (round(s.precision, 2)
                          if s and s.scoreable and s.precision is not None else None),
            "recall": (round(s.recall, 2)
                       if s and s.scoreable and s.recall is not None else None),
            "confirmed": bool(s and s.confirmed),
            "value_claimed": Money.sum(f.value for f in fl).to_rupees_str(),
            "value_verified": (s.verified_value.to_rupees_str()
                               if s and s.scoreable else None),
            "instances": [{"entity_id": f.entity_id,
                           "value": f.value.to_rupees_str(),
                           "derivation": f.derivation} for f in fl],
        })

    refusals = [{
        "bank_txn_id": m.bank_txn_id,
        "evidence": m.evidence,
        "candidates": m.competing,
    } for m in casc.matches if m.reason_code == "AMBIGUOUS_SUBSET"]

    exceptions = []
    for code, cnt in sorted(Counter(m.reason_code for m in casc.matches
                                    if m.reason_code).items(), key=lambda kv: -kv[1]):
        rows = [m for m in casc.matches if m.reason_code == code]
        exceptions.append({"reason_code": code, "count": cnt,
                           "items": [{"bank_txn_id": r.bank_txn_id,
                                      "evidence": r.evidence} for r in rows[:6]]})

    # Health of the two guarantees a reader would otherwise have to take on trust.
    import subprocess
    def _probe(script, ok_marker):
        try:
            r = subprocess.run([sys.executable, str(ROOT / "harness" / script)],
                               capture_output=True, text=True, cwd=ROOT, timeout=120)
            return {"ok": r.returncode == 0 and ok_marker in r.stdout,
                    "output": r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""}
        except Exception as e:
            return {"ok": None, "output": f"{type(e).__name__}: {e}"}

    checks = {
        "ground_truth": _probe("validate_ground_truth.py", "PASS"),
        "model_boundary": _probe("model_layer_check.py", "BOUNDARY HOLDS"),
    }

    bm_path = ROOT / "reports" / "benchmark_llm_matcher.json"
    bm0_path = ROOT / "reports" / "benchmark_llm_matcher_temp0.json"
    bm = json.loads(bm_path.read_text(encoding="utf-8")) if bm_path.exists() else None
    bm0 = json.loads(bm0_path.read_text(encoding="utf-8")) if bm0_path.exists() else None

    total_s = t2 - t_start
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "payments": len({p.payment_id for p in gw.records}),
            "gateway_rows": len(gw.records),
            "bank_rows": len(bank), "refund_rows": len(refunds),
            "adjustment_rows": len(adj),
            "quarantined": len(gw.quarantined),
            "total_records": len(gw.records) + len(bank) + len(refunds) + len(adj),
        },
        "false_match": {
            "scoreable": correct + wrong, "correct": correct, "wrong": wrong,
            "rate": round(wrong / (correct + wrong), 4) if correct + wrong else None,
            "adversarial_rows": len(adv),
            "adversarial_refused": sum(1 for m in adv if m.disposition == EXCEPTION),
        },
        "disposition": {
            "total": n, "auto": auto, "review": rev, "exception": exc,
            "auto_pct": round(100 * auto / n, 1), "review_pct": round(100 * rev / n, 1),
            "exception_pct": round(100 * exc / n, 1),
            "by_tier": dict(sorted(casc.by_tier().items())),
            "human_touch": rev + exc,
            "human_touch_pct": round(100 * (rev + exc) / n, 1),
        },
        "value": {
            "confirmed": confirmed.to_rupees_str(),
            "flagged": flagged.to_rupees_str(),
            "gross": found.total().to_rupees_str(),
            "flagged_pct_of_gross": round(100 * flagged.paise / max(1, found.total().paise)),
            "flagged_composition": [
                {"leak_class": s.leak_class, "precision": round(s.precision, 2),
                 "share_pct": round(100 * s.found_value.paise / max(1, flagged.paise), 1)}
                for s in sorted(flagged_cls, key=lambda z: -z.found_value.paise)],
        },
        "concentration": {
            "top3": Money(concentration["top3_paise"]).to_rupees_str(),
            "top3_pct": concentration["top3_pct"],
            "median": Money(concentration["median_paise"]).to_rupees_str(),
            "count": concentration["count"],
        },
        "findings": findings,
        "refusals": refusals,
        "exceptions": exceptions,
        "ledger": {"entries": len(led), "trial_balance": led.trial_balance().to_rupees_str(),
                   "state_hash": led.state_hash()[:16]},
        "timing": {"cascade_s": round(t1 - t0, 3), "detectors_s": round(t2 - t1, 3),
                   "total_s": round(total_s, 3),
                   "records_per_s": round((len(gw.records) + len(bank)) / max(total_s, 1e-6))},
        "checks": checks,
        "provenance": {
            "fee_schedule": fs.version, "fee_schedule_sha": fs.sha256[:12],
        },
        "benchmark": ({
            "model": bm["model"], "runs": bm["runs"], "records": bm["records"],
            "self_disagreement": bm["self_disagreement_records"],
            "accuracy": bm["llm_correct"], "cascade_accuracy": bm["cascade_correct"],
            "scored": bm["scored_records"],
            "llm_seconds": bm["llm_latency_total_s"], "cascade_seconds": bm["cascade_total_s"],
            "input_tokens": bm["input_tokens"], "output_tokens": bm["output_tokens"],
            "temp0_self_disagreement": bm0["self_disagreement_records"] if bm0 else None,
            "usd_note": bm.get("usd_note"),
        } if bm else None),
    }


if __name__ == "__main__":
    print(json.dumps(build_payload(), indent=2)[:1500])
