#!/usr/bin/env python3
"""Generate the synthetic batch and its ground truth.

One command, seeded, reproducible. Ground truth ships alongside the data so the
scorecard is checkable by anyone, not asserted by the author.

    python3 data/generate.py [--seed N] [--payments N] [--out DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakledger.clock import BusinessCalendar                      # noqa: E402
from leakledger.feeschedule import FeeSchedule                     # noqa: E402
from leakledger.manifest import file_sha256                        # noqa: E402
from leakledger.generate.observers import (                        # noqa: E402
    observe_bank, observe_erp, observe_gateway,
)
from leakledger.generate.world import build_world                  # noqa: E402

# Observer seeds are deliberately unrelated to the world seed and to each other,
# so observation noise in one source cannot correlate with another's.
SEED_GATEWAY, SEED_BANK, SEED_ERP = 8110311, 5520277, 9930433


def write_csv(path: Path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260902)
    ap.add_argument("--payments", type=int, default=500)
    ap.add_argument("--days", type=int, default=22)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "generated")
    args = ap.parse_args()

    cfg = ROOT / "config" / "fee_schedule.v1.json"
    hol = ROOT / "config" / "holidays_2026.json"
    schedule = FeeSchedule.from_file(cfg)
    calendar = BusinessCalendar.from_file(hol)

    world = build_world(
        seed=args.seed, schedule=schedule, calendar=calendar,
        start=date(2026, 6, 1), days=args.days, n_payments=args.payments,
    )
    gateway = observe_gateway(world, SEED_GATEWAY)
    bank = observe_bank(world, SEED_BANK, world.adversarial)
    erp = observe_erp(world, SEED_ERP)

    out = args.out
    write_csv(out / "gateway_payments.csv", gateway, list(gateway[0].keys()))
    write_csv(out / "bank_statement.csv", bank, list(bank[0].keys()))
    write_csv(out / "erp_invoices.csv", erp, list(erp[0].keys()))

    truth = {
        "generator": {
            "world_seed": args.seed,
            "observer_seeds": {"gateway": SEED_GATEWAY, "bank": SEED_BANK, "erp": SEED_ERP},
            "n_payments_requested": args.payments,
            "fee_schedule_version": world.schedule_version,
            "fee_schedule_sha256": world.schedule_sha256,
            "holidays_sha256": file_sha256(hol),
        },
        "counts": {
            "gateway_rows": len(gateway), "bank_rows": len(bank), "erp_rows": len(erp),
            "total_rows": len(gateway) + len(bank) + len(erp),
            "payments": len(world.payments), "settlements": len(world.settlements),
            "refunds": len(world.refunds), "chargebacks": len(world.chargebacks),
            "invoices": len(world.invoices),
            "seeded_leaks": len(world.seeded_leaks),
            "adversarial_cases": len(world.adversarial),
        },
        "settlements": [
            {
                "settlement_id": s.settlement_id, "utr": s.utr,
                "cycle_date": s.cycle_date.isoformat(), "value_date": s.value_date.isoformat(),
                "payment_ids": s.payment_ids, "paid": s.paid, "duplicated": s.duplicated,
                "gross_paise": s.gross.paise, "fee_paise": s.fee.paise, "gst_paise": s.gst.paise,
                "refunds_paise": s.refunds.paise, "chargebacks_paise": s.chargebacks.paise,
                "reserve_held_paise": s.reserve_held.paise, "net_paise": s.net.paise,
                "case_tags": s.case_tags,
            }
            for s in world.settlements
        ],
        "payment_settlement_links": {
            p.payment_id: p.settlement_id for p in world.payments if p.settlement_id
        },
        "payment_invoice_links": [
            {"invoice_id": i.invoice_id, "payment_ids": i.payment_ids,
             "amount_paise": i.amount.paise, "case_tags": i.case_tags}
            for i in world.invoices
        ],
        "expected_fees": {
            p.payment_id: {
                "instrument": p.instrument, "amount_paise": p.amount.paise,
                "expected_fee_paise": p.expected_fee.paise,
                "expected_gst_paise": p.expected_gst.paise,
                "charged_fee_paise": p.fee_charged.paise,
                "charged_gst_paise": p.gst_charged.paise,
            }
            for p in world.payments
        },
        "seeded_leaks": [
            {"leak_id": l.leak_id, "class": l.leak_class, "entity_id": l.entity_id,
             "value_paise": l.value.paise, "detail": l.detail}
            for l in world.seeded_leaks
        ],
        "adversarial_cases": [
            {"case_id": a.case_id, "type": a.case_type,
             "expected_behaviour": a.expected_behaviour,
             "entity_ids": a.entity_ids, "detail": a.detail}
            for a in world.adversarial
        ],
    }
    gt = out / "ground_truth.json"
    gt.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    total_leak = sum(l["value_paise"] for l in truth["seeded_leaks"])
    print(f"gateway_payments.csv  {len(gateway):>5} rows")
    print(f"bank_statement.csv    {len(bank):>5} rows")
    print(f"erp_invoices.csv      {len(erp):>5} rows")
    print(f"                      {truth['counts']['total_rows']:>5} records total")
    print(f"ground_truth.json     {len(truth['seeded_leaks'])} seeded leaks "
          f"(Rs {total_leak/100:,.2f}), {len(truth['adversarial_cases'])} adversarial cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
