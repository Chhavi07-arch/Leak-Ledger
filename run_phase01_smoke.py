"""Phase 01 smoke: exercise every foundation module end to end and leave real
artefacts on disk (audit log + run manifest). Not a demo — a wiring check."""
import sys, pathlib
sys.path.insert(0, "src")
from leakledger.audit import AuditLog
from leakledger.clock import BusinessCalendar, expected_settlement_date, parse_ist
from leakledger.feeschedule import FeeSchedule
from leakledger.manifest import RunManifest
from leakledger.money import Money
from leakledger.schema import ingest_rows

CFG = pathlib.Path("config/fee_schedule.v1.json")
HOL = pathlib.Path("config/holidays_2026.json")
OUT = pathlib.Path("reports/phase01")

manifest = RunManifest.start(config_paths=[CFG, HOL], run_id="phase01_smoke")
fs = FeeSchedule.from_file(CFG)
cal = BusinessCalendar.from_file(HOL)
audit = AuditLog(OUT / "audit.jsonl", run_id=manifest.run_id)

rows = [
    {"payment_id": "pay_001", "order_id": "ord_001", "rrn": "123456789012",
     "captured_at": "2026-06-12T23:58:00", "amount": "4500.00", "instrument": "DEBIT_CARD",
     "status": "CAPTURED", "fee_charged": "58.00", "gst_charged": "10.44"},
    {"payment_id": "pay_002", "order_id": "ord_002", "captured_at": "2026-06-12T10:00:00",
     "amount": "999.00", "instrument": "UPI", "status": "CAPTURED",
     "fee_charged": "0.00", "gst_charged": "0.00"},
    {"payment_id": "pay_003", "order_id": "ord_003", "captured_at": "2026-06-12T11:00:00",
     "amount": "1200.00", "instrument": "NETBANKING", "bank": "SBI", "status": "CAPTURED",
     "fee_charged": "18.00", "gst_charged": "3.24"},
    {"payment_id": "pay_004", "order_id": "ord_004", "captured_at": "2026-06-12T12:00:00",
     "amount": "12.345", "instrument": "UPI", "status": "CAPTURED"},
    {"payment_id": "pay_005", "order_id": "ord_005", "captured_at": "2026-06-12T13:00:00",
     "amount": "500.00", "instrument": "AMEX_CORP", "status": "CAPTURED"},
]

res = ingest_rows("gateway", rows)
print(f"INGEST   {res.summary()}")
for q in res.quarantined:
    audit.append(stage="ingest", record_id=f"row{q.row_num}", decision="QUARANTINE",
                 reason_code=q.reason_code, evidence=q.raw, detail_text=q.detail)
    print(f"  QUARANTINE row {q.row_num}: {q.reason_code} — {q.detail}")

print(f"\nFEE DECOMPOSITION (schedule {fs.version} sha {fs.sha256[:12]}, "
      f"gst policy '{fs.gst_rounding_policy}')")
for rec in res.records:
    try:
        comp = fs.expected_fee(rec.instrument, rec.amount,
                               is_international=rec.is_international, bank=rec.bank)
    except Exception as e:
        audit.append(stage="decompose", record_id=rec.payment_id, decision="EXCEPTION",
                     reason_code="FEE_SLAB_UNKNOWN", evidence={"instrument": rec.instrument})
        print(f"  {rec.payment_id}  EXCEPTION FEE_SLAB_UNKNOWN — {e}")
        continue
    settle = expected_settlement_date(rec.captured_at, cal)
    delta = (rec.fee_charged - comp.fee) if rec.fee_charged is not None else Money.zero()
    verdict = "MATCHES_CONTRACT" if delta.paise == 0 else "FEE_DELTA"
    audit.append(stage="decompose", record_id=rec.payment_id, decision=verdict,
                 reason_code=None if delta.paise == 0 else "FEE_OVERCHARGE",
                 tier=None, evidence={"amount": rec.amount.paise, "slab": comp.slab_label},
                 expected_fee_paise=comp.fee.paise,
                 actual_fee_paise=rec.fee_charged.paise if rec.fee_charged else None,
                 delta_paise=delta.paise, expected_settlement=str(settle))
    print(f"  {rec.payment_id}  captured {rec.captured_at.isoformat()}  -> settles {settle}")
    print(f"            {comp.derivation()}")
    if delta.paise:
        print(f"            actual fee Rs {rec.fee_charged.to_rupees_str()}  "
              f"DELTA Rs {delta.to_rupees_str()}  [{verdict}]")

manifest.note(f"phase01 smoke: {res.summary()}")
manifest.note(f"audit entries: {len(audit)}")
mpath = manifest.write(OUT / "manifest.json")
print(f"\nAUDIT    {len(audit)} entries -> {audit.path}")
print(f"MANIFEST -> {mpath}  (commit={manifest.git_commit}, dirty={manifest.git_dirty})")
