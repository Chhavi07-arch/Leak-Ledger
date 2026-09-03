#!/usr/bin/env python3
"""The scorecard. Every number reported by the submission is produced here.

PLAN.md's bar for this track is throughput + measured accuracy + an honest
exception list, and all three are printed below from measurement -- nothing in
this file is typed by hand.

Two reporting rules are structural, not stylistic:

  PER CLASS, NEVER AGGREGATE. An aggregate precision hides the classes that do
  not work, and would let ZERO_MDR_VIOLATION -- precision 1.0 by construction --
  carry classes that are genuinely weak.

  CONTRACT-DEPENDENT FINDINGS ARE REPORTED SEPARATELY. They compare against a fee
  schedule authored in this repository from which the data was also generated.
  They verify arithmetic; they do not discover anything.
"""
from __future__ import annotations

import csv, hashlib, json, statistics, subprocess, sys, time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                                  # noqa: E402
from leakledger.feeschedule import FeeSchedule                                 # noqa: E402
from leakledger.ledger import Ledger, apply_run                                # noqa: E402
from leakledger.money import Money                                             # noqa: E402
from leakledger.schema import ingest_rows                                      # noqa: E402
from leakledger.cascade.engine import (                                        # noqa: E402
    AUTO_APPLY, EXCEPTION, REVIEW, Cascade, covered_cycles_by_matching)
from leakledger.leakage import detectors                                       # noqa: E402
from leakledger.leakage.findings import (                                      # noqa: E402
    CONTRACT_DEPENDENT, EXCEPTION_SIGNAL, RULE_CHECK, STRUCTURAL)
sys.path.insert(0, str(ROOT / "harness"))
from scoring import headline_split, score_all                                   # noqa: E402

DATA = ROOT / "data" / "generated"
AS_OF = date(2026, 7, 31)
_load = lambda n: list(csv.DictReader((DATA / n).open(encoding="utf-8")))


# ---------------------------------------------------------------------
#  entity-id mapping: finding-space <-> ground-truth-space
# ---------------------------------------------------------------------
def build_entity_map(truth, bank_rows):
    """Map a finding's identifier onto the identifier ground truth uses.

    The two namespaces differ on purpose. DUPLICATE_PAYOUT names a BANK
    TRANSACTION, because that is what a human opens and acts on; ground truth
    names the SETTLEMENT that was paid twice, because that is what was seeded.
    Relabelling findings to match the fixture would damage the product to please
    the scorer, so the scorer carries the mapping instead.
    """
    by_net = defaultdict(list)
    for s in truth["settlements"]:
        by_net[s["net_paise"]].append(s["settlement_id"])
    txn_to_settlement = {}
    for b in bank_rows:
        if b["direction"] != "CR":
            continue
        cands = by_net.get(Money.from_rupees_str(b["amount"]).paise, [])
        if len(cands) == 1:
            txn_to_settlement[b["txn_id"]] = cands[0]
    return txn_to_settlement


def score_class(cls, findings, seeded, truth, entity_map):
    ids = {f.entity_id for f in findings}
    if cls == "DUPLICATE_PAYOUT":
        ids = {entity_map.get(i, i) for i in ids}
    if cls == "MISSING_SETTLEMENT":
        unpaid = {p for s in truth["settlements"] if not s["paid"] for p in s["payment_ids"]}
        n_unpaid_cycles = sum(1 for s in truth["settlements"] if not s["paid"])
        tp = sum(1 for f in findings if set(f.evidence.get("payment_ids", [])) & unpaid)
        return tp, len(findings) - tp, max(0, n_unpaid_cycles - tp)
    s = seeded.get(cls, set())
    return len(ids & s), len(ids - s), len(s - ids)


def run_once(bound=None):
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
    return dict(gw=gw, bank=bank, refunds=refunds, adj=adj, casc=casc, found=found,
                cascade_s=t1 - t0, detect_s=t2 - t1)


def main():
    truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
    r = run_once()
    casc, found, bank, gw = r["casc"], r["found"], r["bank"], r["gw"]
    seeded = defaultdict(set)
    for l in truth["seeded_leaks"]:
        seeded[l["class"]].add(l["entity_id"])
    seeded_value = defaultdict(int)
    for l in truth["seeded_leaks"]:
        seeded_value[l["class"]] += l["value_paise"]
    scores = score_all(found, truth, bank)

    W = 78
    print("=" * W)
    print("LEAK LEDGER — SCORECARD".center(W))
    print("=" * W)

    # ---------- 1. FALSE-MATCH RATE (primary) ----------
    by_net = defaultdict(list)
    for s in truth["settlements"]:
        if s["paid"]:
            by_net[s["net_paise"]].append(s)
    correct = wrong = unscoreable = 0
    adv_correct = adv_wrong = 0
    adv_txns = {b["txn_id"] for b in bank if b["txn_id"].endswith("X")}
    for m in casc.matches:
        if m.tier != "T3" or m.disposition not in (AUTO_APPLY, REVIEW):
            continue
        b = next(x for x in bank if x["txn_id"] == m.bank_txn_id)
        cands = by_net.get(Money.from_rupees_str(b["amount"]).paise, [])
        if len(cands) != 1:
            unscoreable += 1
            continue
        ok = set(cands[0]["payment_ids"]) == set(m.matched_ids)
        correct += ok
        wrong += not ok
    for m in casc.matches:
        if m.bank_txn_id in adv_txns:
            adv_correct += m.disposition == EXCEPTION
            adv_wrong += m.disposition != EXCEPTION
    total_scored = correct + wrong
    print()
    print("1. FALSE-MATCH RATE  [PRIMARY METRIC]")
    print(f"   scoreable T3 matches      : {total_scored}")
    print(f"   correct payment sets      : {correct}")
    print(f"   WRONG payment sets        : {wrong}")
    print(f"   false-match rate          : {wrong/total_scored:.4f}" if total_scored
          else "   false-match rate          : n/a")
    print(f"   unscoreable (net not unique in truth): {unscoreable}")
    print()
    print("   adversarial population (UTR reused with altered amount):")
    print(f"     rows                    : {adv_correct + adv_wrong}")
    print(f"     correctly refused       : {adv_correct}")
    print(f"     wrongly accepted        : {adv_wrong}")
    print(f"     adversarial false-match rate: "
          f"{adv_wrong/(adv_correct+adv_wrong):.4f}" if (adv_correct + adv_wrong)
          else "     adversarial false-match rate: n/a")

    # ---------- 2. DISPOSITION ----------
    n = len(casc.matches)
    auto, rev, exc = (len(casc.by_disposition(d)) for d in (AUTO_APPLY, REVIEW, EXCEPTION))
    print()
    print("2. DISPOSITION")
    print(f"   bank rows processed       : {n}")
    print(f"   auto-applied              : {auto:>3}  ({100*auto/n:5.1f}%)")
    print(f"   review queue              : {rev:>3}  ({100*rev/n:5.1f}%)")
    print(f"   exceptions                : {exc:>3}  ({100*exc/n:5.1f}%)")
    print(f"   by tier                   : {dict(sorted(casc.by_tier().items()))}")

    # ---------- 3. PER-CLASS PRECISION / RECALL ----------
    print()
    print("3. LEAKAGE — PER CLASS (never aggregated)")
    by_class = found.by_class()
    for label, cat in (("STRUCTURAL — no contract consulted", STRUCTURAL),
                       ("CONTRACT-DEPENDENT — verification, not discovery", CONTRACT_DEPENDENT),
                       ("RULE CHECK — precision 1.0 by construction, excluded from scoring", RULE_CHECK),
                       ("EXCEPTION SIGNAL — no value claimed", EXCEPTION_SIGNAL)):
        rows = sorted(c for c in by_class if by_class[c][0].category == cat)
        if not rows:
            continue
        print(f"\n   {label}")
        print(f"   {'class':28} {'TP':>3} {'FP':>3} {'FN':>3} {'prec':>6} {'rec':>6} "
              f"{'Rs claimed':>13} {'Rs verified':>13}")
        for cls in rows:
            fl = by_class[cls]
            s = scores[cls]
            prec = s.precision if s.precision is not None else float("nan")
            rec = s.recall if s.recall is not None else float("nan")
            flag = "" if (s.confirmed or not s.scoreable) else "  << FP > 0"
            print(f"   {cls:28} {s.tp:>3} {s.fp:>3} {s.fn:>3} {prec:>6.2f} {rec:>6.2f} "
                  f"{s.found_value.to_rupees_str():>13} {s.verified_value.to_rupees_str():>13}"
                  f"{flag}")

    # ---------- 4. VALUE ----------
    print()
    print("4. LEAKAGE VALUE")
    conf, flag, flag_cls = headline_split(found, scores)
    print(f"   {'CONFIRMED':22} Rs {conf.to_rupees_str():>14}   "
          f"every instance verified against ground truth")
    print(f"   {'FLAGGED, not confirmed':22} Rs {flag.to_rupees_str():>14}   "
          f"{', '.join(s.leak_class for s in flag_cls)}")
    for s in sorted(flag_cls, key=lambda z: -z.found_value.paise):
        print(f"     {s.leak_class:26} precision {s.precision:.2f}  "
              f"{100*s.found_value.paise/max(1,flag.paise):5.1f}% of the flagged value")
    print(f"   {'gross of both':22} Rs {found.total().to_rupees_str():>14}   "
          f"({100*flag.paise/max(1,found.total().paise):.0f}% from classes with precision < 1.00)")
    print("   value claimed sums EVERY reported instance, not only verified ones:")
    print("   that is what the engine claims at run time, having no ground truth of its own.")
    for label, cat in (("  structural", STRUCTURAL), ("  contract-dependent", CONTRACT_DEPENDENT),
                       ("  rule check", RULE_CHECK)):
        print(f"   {label:22} Rs {found.total(cat).to_rupees_str():>14}")
    vals = sorted((f.value.paise for f in found.findings if f.value.paise), reverse=True)
    if vals:
        top3 = sum(vals[:3])
        print(f"   concentration            : top 3 findings = Rs {top3/100:,.2f} "
              f"({100*top3/sum(vals):.1f}% of total)")
        print(f"   median finding           : Rs {statistics.median(vals)/100:,.2f}")

    # ---------- 5. HUMAN TOUCH ----------
    touched = rev + exc
    print()
    print("5. HUMAN-TOUCH RATE  [secondary — a ratio, not a time claim]")
    print(f"   items needing a human     : {touched} of {n}  ({100*touched/n:.1f}%)")
    print(f"   resolved without a human  : {auto} of {n}  ({100*auto/n:.1f}%)")

    # ---------- 6. THROUGHPUT ----------
    recs = len(gw.records) + len(bank) + len(r["refunds"]) + len(r["adj"])
    total_s = r["cascade_s"] + r["detect_s"]
    print()
    print("6. THROUGHPUT  [deterministic core, no model layer]")
    print(f"   records in scope          : {recs}")
    print(f"   cascade                   : {r['cascade_s']:.3f}s")
    print(f"   detectors                 : {r['detect_s']:.3f}s")
    print(f"   end to end                : {total_s:.3f}s  ({recs/total_s:,.0f} records/s)")

    # ---------- 7. EXCEPTION LIST ----------
    print()
    print("7. EXCEPTION LIST  [the honest deliverable]")
    reasons = Counter(m.reason_code for m in casc.matches if m.reason_code)
    for code, cnt in sorted(reasons.items(), key=lambda kv: -kv[1]):
        ex = next(m for m in casc.matches if m.reason_code == code)
        print(f"   [{code:26}] {cnt:>2}  {ex.evidence[:80]}")
    print(f"   quarantined at ingest      : {len(gw.quarantined)}")

    # ---------- 8. LEDGER ----------
    led = Ledger()
    stats = apply_run(led, run_id="score", cascade_result=casc, findings=found,
                      payments=gw.records)
    print()
    print("8. LEDGER")
    print(f"   entries posted            : {stats['posted']}")
    print(f"   trial balance             : {led.trial_balance()}  (must be Rs 0.00)")
    print(f"   state hash                : {led.state_hash()[:16]}")
    print("=" * W)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
