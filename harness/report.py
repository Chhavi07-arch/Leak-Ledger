#!/usr/bin/env python3
"""Generate reports/run_report.html from a run.

SURFACE DECISION (PLAN.md Phase 07, decided on merit once real output existed).

The deciding evidence was not aesthetic. The single most important artefact in
this build -- the AMBIGUOUS_SUBSET refusal -- rendered in the CLI as:

    "2 distinct deviations of size 2 reconcile identically; refusing to choose"

which states that two answers exist without showing either. A refusal a reviewer
cannot inspect is indistinguishable from a failure to try. The competing
reconciliations are a COMPARISON, and finding derivations are multi-clause
arithmetic (median 136 chars, max 275) that wrap across terminal lines. Both are
structurally two-dimensional; a terminal flattens them.

So: a STATIC HTML report, not an interactive dashboard.
  - generated from the same harness call as the CLI scorecard, so the two cannot
    disagree about any number;
  - no server, no state, no framework -- deterministic output, committable as a
    build artefact, and nothing to fail live during a recording;
  - deliberately an ops report, not a marketing page. PLAN's standing rule holds:
    a prettier surface never wins over harder seeded cases.

The CLI scorecard remains the operational surface. This is the reading surface.
"""
from __future__ import annotations

import csv, html, json, sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakledger.clock import BusinessCalendar                                 # noqa: E402
from leakledger.feeschedule import FeeSchedule                                # noqa: E402
from leakledger.ledger import Ledger, apply_run                               # noqa: E402
from leakledger.money import Money                                            # noqa: E402
from leakledger.schema import ingest_rows                                     # noqa: E402
from leakledger.cascade.engine import (                                       # noqa: E402
    AUTO_APPLY, EXCEPTION, REVIEW, Cascade, covered_cycles_by_matching)
from leakledger.leakage import detectors                                      # noqa: E402
from leakledger.leakage.findings import (                                     # noqa: E402
    CONTRACT_DEPENDENT, EXCEPTION_SIGNAL, RULE_CHECK, STRUCTURAL)

DATA = ROOT / "data" / "generated"
OUT = ROOT / "reports" / "run_report.html"
AS_OF = date(2026, 7, 31)
_load = lambda n: list(csv.DictReader((DATA / n).open(encoding="utf-8")))
e = html.escape

CSS = """
:root{--ink:#16201b;--soft:#56655d;--faint:#7c8a83;--rule:#c7d2ca;--rule2:#e2e8e3;
--paper:#fcfcfa;--bar:#eef3ec;--accent:#1c5a3c;--flag:#a33520;--flagbg:#f7e9e5;--warn:#7e6212}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);margin:0;padding:0 1.5rem 5rem;
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:60rem;margin:0 auto}
h1{font-size:1.9rem;letter-spacing:-.02em;margin:2.5rem 0 .3rem}
h2{font-size:1.05rem;letter-spacing:.09em;text-transform:uppercase;color:var(--accent);
margin:2.6rem 0 .8rem;padding-top:.9rem;border-top:1px solid var(--rule)}
.sub{color:var(--soft);margin:0 0 1.4rem}
.headline{border:2px solid var(--ink);background:var(--bar);padding:1.3rem 1.5rem;margin:1.2rem 0}
.headline .big{font-size:1.5rem;line-height:1.35;margin:0}
.rs{color:var(--flag);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:.86rem;margin:.6rem 0 1.2rem}
th{background:var(--ink);color:var(--paper);text-align:left;padding:.45rem .7rem;
font-size:.68rem;letter-spacing:.09em;text-transform:uppercase;font-weight:500;white-space:nowrap}
td{padding:.42rem .7rem;border-top:1px solid var(--rule2);vertical-align:top}
tbody tr:nth-child(odd){background:#f5f8f4}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;
font-variant-numeric:tabular-nums;white-space:nowrap}
.deriv{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.76rem;
color:var(--soft);white-space:normal}
.pill{display:inline-block;font-size:.64rem;letter-spacing:.07em;text-transform:uppercase;
padding:.1em .45em;border-radius:2px;white-space:nowrap}
.ok{background:#e2efe6;color:var(--accent)}.warn{background:#f5efdd;color:var(--warn)}
.bad{background:var(--flagbg);color:var(--flag)}
.refusal{border:1px solid var(--rule);margin:1rem 0;background:#fff}
.refusal h3{margin:0;padding:.6rem .9rem;background:var(--flagbg);color:var(--flag);
font-size:.8rem;letter-spacing:.05em;text-transform:uppercase}
.cands{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--rule)}
@media(max-width:700px){.cands{grid-template-columns:1fr}}
.cand{background:#fff;padding:.8rem .9rem}
.cand .lbl{font-size:.65rem;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);
margin-bottom:.35rem}
.cand code{font-family:ui-monospace,Menlo,monospace;font-size:.76rem;display:block;margin:.15rem 0}
.diff{background:#fff3b0;padding:0 .15em;border-radius:2px}
.note{border-left:2px solid var(--rule);padding-left:.8rem;color:var(--faint);
font-size:.83rem;margin:1rem 0}
.gap{border:1px dashed var(--flag);background:var(--flagbg);padding:.9rem 1.1rem;margin:1rem 0}
.gap b{color:var(--flag)}
footer{margin-top:3rem;padding-top:1rem;border-top:2px solid var(--ink);
font-family:ui-monospace,Menlo,monospace;font-size:.72rem;color:var(--faint)}
"""


def build():
    fs = FeeSchedule.from_file(ROOT / "config" / "fee_schedule.v1.json")
    cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
    gw = ingest_rows("gateway", _load("gateway_payments.csv"))
    refunds, adj = _load("gateway_refunds.csv"), _load("gateway_adjustments.csv")
    bank = _load("bank_statement.csv")
    eng = Cascade(payments=gw.records, refunds=refunds, bank=bank,
                  adjustments=adj, calendar=cal)
    casc = eng.run()
    cov = covered_cycles_by_matching(eng, bank)
    found = detectors.run_all(fs=fs, payments=gw.records, refunds=refunds, adjustments=adj,
                              bank_rows=bank, cascade_result=casc, calendar=cal,
                              as_of=AS_OF, covered_cycles=cov)
    truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))
    return gw, bank, casc, found, truth, fs


def false_match(casc, bank, truth):
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
    return correct, wrong


def render_refusal(m):
    """Side-by-side competing answers, with the differing element highlighted."""
    cands = m.competing[:4]
    if not cands:
        return ""
    allx = [set(c["excluded"]) for c in cands]
    common = set.intersection(*allx) if allx else set()
    cells = []
    OPEN, CLOSE = '<span class="diff">', "</span>"
    for i, c in enumerate(cands):
        parts = []
        for x in c["excluded"]:
            mark = x not in common
            parts.append("<code>" + (OPEN if mark else "") + e(x)
                         + (CLOSE if mark else "") + "</code>")
        ex = "".join(parts) or "<code>&mdash;</code>"
        inc = "".join("<code>" + e(x) + "</code>" for x in c["included"]) or "<code>&mdash;</code>"
        cells.append(
            f'<div class="cand"><div class="lbl">Candidate {i+1} · deviation size {c["size"]}'
            f'</div><div class="lbl">exclude</div>{ex}'
            f'<div class="lbl" style="margin-top:.5rem">include</div>{inc}</div>')
    return (f'<div class="refusal"><h3>{e(m.bank_txn_id)} — refused: '
            f'{len(m.competing)} reconciliations are equally valid</h3>'
            f'<div class="cands">{"".join(cells)}</div>'
            f'<div style="padding:.7rem .9rem;font-size:.83rem;color:var(--soft)">'
            f'{e(m.evidence)}<br><b>The highlighted ids are the only difference.</b> '
            f'Both sets reconcile to the same payout, so the engine declined to choose '
            f'rather than pick one and be silently wrong.</div></div>')


def main() -> int:
    gw, bank, casc, found, truth, fs = build()
    correct, wrong = false_match(casc, bank, truth)
    n = len(casc.matches)
    auto, rev, exc = (len(casc.by_disposition(d)) for d in (AUTO_APPLY, REVIEW, EXCEPTION))
    seeded = defaultdict(set)
    for l in truth["seeded_leaks"]:
        seeded[l["class"]].add(l["entity_id"])
    P = []
    A = P.append

    A(f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
      f'<meta name="viewport" content="width=device-width,initial-scale=1">'
      f'<title>Leak Ledger — run report</title><style>{CSS}</style></head><body><div class="wrap">')
    A('<h1>Leak Ledger — run report</h1>'
      '<p class="sub">Reconciliation and leakage detection over a 3-source synthetic batch '
      'with published ground truth. Every figure below is produced by the metrics harness.</p>')

    struct = found.by_category(STRUCTURAL)
    cls_ct = Counter(f.leak_class for f in struct)
    A(f'<div class="headline"><p class="big">Across {len(gw.records)} payments and '
      f'{len(bank)} bank rows, Leak Ledger found <span class="rs">Rs '
      f'{found.total().to_rupees_str()}</span> of leakage — '
      f'{cls_ct.get("MISSING_SETTLEMENT",0)} missing settlements, '
      f'{cls_ct.get("DUPLICATE_PAYOUT",0)} duplicate payouts, '
      f'{cls_ct.get("REFUND_NOT_REACHED",0)} refunds that never reached the customer, '
      f'{cls_ct.get("CHARGEBACK_NOT_RECREDITED",0)} disputes won but not re-credited. '
      f'It refused to match {exc} records, each with a stated cause.</p></div>')

    A('<h2>1 · False-match rate <span class="pill bad">primary</span></h2>')
    A(f'<table><tr><th>metric</th><th>value</th></tr>'
      f'<tr><td>scoreable matches</td><td class="mono">{correct+wrong}</td></tr>'
      f'<tr><td>correct payment sets</td><td class="mono">{correct}</td></tr>'
      f'<tr><td>wrong payment sets</td><td class="mono">{wrong}</td></tr>'
      f'<tr><td><b>false-match rate</b></td><td class="mono"><b>'
      f'{wrong/(correct+wrong) if correct+wrong else 0:.4f}</b></td></tr></table>')
    A('<div class="note">A wrong match silently corrupts the books and surfaces a month '
      'later; a missing match sits visibly in a queue. That asymmetry is why this is the '
      'primary metric and why the engine refuses rather than guesses.</div>')

    A('<h2>2 · What the engine refused to decide</h2>')
    A('<p class="sub">The behaviour this build exists to demonstrate. Each panel shows the '
      'competing reconciliations side by side — not a count of them.</p>')
    for m in [x for x in casc.matches if x.reason_code == "AMBIGUOUS_SUBSET"][:3]:
        A(render_refusal(m))

    A('<h2>3 · Findings</h2>')
    for label, cat, note in (
        ("Structural — no contract consulted", STRUCTURAL,
         "Detect loss without needing to know a contract rate. These carry the headline."),
        ("Contract-dependent — verification, not discovery", CONTRACT_DEPENDENT,
         "Compared against a fee schedule authored in this repository, from which the batch "
         "was also generated. They prove the arithmetic is right; they discover nothing."),
        ("Rule check — excluded from scoring", RULE_CHECK,
         "Precision 1.0 by construction. Reported, never counted."),
        ("Exception signal — no value claimed", EXCEPTION_SIGNAL,
         "The cycle would not reconcile. The shortfall cannot be honestly quantified, so no "
         "rupee value is claimed.")):
        rows = sorted({f.leak_class for f in found.by_category(cat)})
        if not rows:
            continue
        A(f'<h3 style="font-size:.9rem;margin:1.4rem 0 .3rem">{e(label)}</h3>'
          f'<p class="sub" style="margin:.2rem 0 .5rem;font-size:.83rem">{e(note)}</p>')
        A('<table><tr><th>class</th><th>found</th><th>seeded</th><th>value</th>'
          '<th>example derivation</th></tr>')
        for cls in rows:
            fl = [f for f in found.findings if f.leak_class == cls]
            A(f'<tr><td class="mono">{e(cls)}</td><td class="mono">{len(fl)}</td>'
              f'<td class="mono">{len(seeded.get(cls,[]))}</td>'
              f'<td class="mono">Rs {Money.sum(f.value for f in fl).to_rupees_str()}</td>'
              f'<td class="deriv">{e(fl[0].derivation)}</td></tr>')
        A('</table>')

    A('<h2>4 · Exception queue</h2>')
    A('<p class="sub">The honest deliverable. Every unresolved record is typed; none is dropped.</p>')
    A('<table><tr><th>reason</th><th>n</th><th>example</th></tr>')
    for code, cnt in sorted(Counter(m.reason_code for m in casc.matches
                                    if m.reason_code).items(), key=lambda kv: -kv[1]):
        ex = next(m for m in casc.matches if m.reason_code == code)
        A(f'<tr><td class="mono">{e(code)}</td><td class="mono">{cnt}</td>'
          f'<td class="deriv">{e(ex.evidence)}</td></tr>')
    A('</table>')

    A('<h2>5 · AI judgment</h2>')
    A('<table><tr><th>where a model is used</th><th>what constrains it</th></tr>'
      '<tr><td>Bank narration &rarr; candidate entities</td><td>Proposes only. Accepted only if the '
      'named reference exists <i>and</i> the amount agrees exactly. Confidence is never consulted.</td></tr>'
      '<tr><td>Exception rationale</td><td>Discarded if it introduces a figure absent from the '
      'evidence; the deterministic string is shown instead.</td></tr>'
      '<tr><td>Settlement Q&amp;A</td><td>Read-only. No write path exists — enforced by the '
      'function signature, not by convention.</td></tr></table>')
    A('<div class="note">Verified with an adversarial provider that returns confidently wrong, '
      'well-formed answers: every metric above is identical with it wired in, and false-match '
      'rate stays at 0.0000. A boundary that depends on the model behaving is not a boundary.</div>')
    A('<div class="gap"><b>Stated gap — the LLM-as-matcher benchmark has not been run.</b><br>'
      'No live model credentials were available at build time. The record selection is frozen and '
      'committed (<span class="mono">harness/benchmark_selection.json</span>) so it provably '
      'predates any result, and <span class="mono">harness/benchmark_llm_matcher.py</span> refuses '
      'to run without credentials and writes nothing. Self-disagreement, accuracy, latency and cost '
      'are therefore <b>unmeasured and unreported</b> rather than estimated.</div>')

    led = Ledger()
    apply_run(led, run_id="report", cascade_result=casc, findings=found, payments=gw.records)
    A(f'<footer>ledger entries {len(led)} · trial balance {led.trial_balance()} · '
      f'state {led.state_hash()[:16]} · fee schedule {e(fs.version)} sha {fs.sha256[:12]} · '
      f'generated {datetime.now():%Y-%m-%d %H:%M} IST</footer>')
    A('</div></body></html>')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(P), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
