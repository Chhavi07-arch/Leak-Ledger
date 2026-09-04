"""Canonical records for the three sources, and typed quarantine.

The three sources are deliberately divergent — a gateway export, a bank
statement with free-text narration, and an ERP invoice register. They share no
clean join key. Normalising them into one shape is the first real step of
reconciliation; if they arrived joinable, this would be a join, not a reconciler.

No row is ever dropped. A row that fails validation becomes a QuarantinedRow
with a typed reason and is counted in every report. Silent drops are how
reconciliation systems lie: the totals tie because the awkward rows vanished.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from .clock import ClockError, parse_ist, parse_ist_date
from .money import Money, MoneyError

# --- typed quarantine reasons ------------------------------------------
MISSING_FIELD = "MISSING_FIELD"
BAD_AMOUNT = "BAD_AMOUNT"
BAD_TIMESTAMP = "BAD_TIMESTAMP"
BAD_ENUM = "BAD_ENUM"

INSTRUMENTS = ("UPI", "DEBIT_CARD", "CREDIT_CARD", "AMEX", "WALLET", "EMI", "NETBANKING")
PAYMENT_STATUSES = ("CAPTURED", "AUTHORIZED", "FAILED", "REFUNDED")
DIRECTIONS = ("CR", "DR")
INVOICE_STATUSES = ("OPEN", "PAID", "PARTIAL", "CANCELLED")


@dataclass(frozen=True)
class QuarantinedRow:
    source: str
    row_num: int
    reason_code: str
    detail: str
    raw: Dict[str, Any]


@dataclass(frozen=True)
class GatewayPayment:
    """A payment leg as the gateway reports it."""
    payment_id: str
    order_id: str
    rrn: Optional[str]
    captured_at: datetime
    amount: Money
    instrument: str
    bank: Optional[str]
    is_international: bool
    status: str
    fee_charged: Optional[Money]
    gst_charged: Optional[Money]
    tds_withheld: Optional[Money]
    source: str = "gateway"
    row_num: int = -1


@dataclass(frozen=True)
class BankTransaction:
    """A line on the bank statement. Narration is free text by nature."""
    txn_id: str
    value_date: date
    amount: Money
    direction: str
    utr: Optional[str]
    narration: str
    source: str = "bank"
    row_num: int = -1


@dataclass(frozen=True)
class ErpInvoice:
    """An invoice as the merchant's own books record it."""
    invoice_id: str
    issued_date: date
    amount: Money
    counterparty: str
    status: str
    source: str = "erp"
    row_num: int = -1


@dataclass
class IngestResult:
    """Records that parsed, plus every row that did not — both counted."""
    records: List[Any] = field(default_factory=list)
    quarantined: List[QuarantinedRow] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return len(self.records) + len(self.quarantined)

    def summary(self) -> str:
        return (
            f"{len(self.records)} parsed, {len(self.quarantined)} quarantined, "
            f"{self.total_rows} rows seen"
        )


# --- helpers ------------------------------------------------------------

def _require(row: Dict[str, Any], key: str) -> Tuple[Optional[str], Optional[str]]:
    val = row.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        return None, f"required field {key!r} is empty"
    return str(val).strip(), None


def _optional_money(row: Dict[str, Any], key: str) -> Tuple[Optional[Money], Optional[str]]:
    val = row.get(key)
    if val is None or (isinstance(val, str) and not val.strip()):
        return None, None
    try:
        return Money.from_rupees_str(str(val)), None
    except MoneyError as e:
        return None, f"{key}: {e}"


def _q(source: str, row_num: int, code: str, detail: str, raw: Dict[str, Any]) -> QuarantinedRow:
    return QuarantinedRow(source=source, row_num=row_num, reason_code=code, detail=detail, raw=dict(raw))


# --- parsers ------------------------------------------------------------

def parse_gateway_row(row: Dict[str, Any], row_num: int):
    for key in ("payment_id", "order_id", "captured_at", "amount", "instrument", "status"):
        val, err = _require(row, key)
        if err:
            return _q("gateway", row_num, MISSING_FIELD, err, row)

    try:
        amount = Money.from_rupees_str(str(row["amount"]))
    except MoneyError as e:
        return _q("gateway", row_num, BAD_AMOUNT, f"amount: {e}", row)

    try:
        captured_at = parse_ist(str(row["captured_at"]))
    except ClockError as e:
        return _q("gateway", row_num, BAD_TIMESTAMP, f"captured_at: {e}", row)

    instrument = str(row["instrument"]).strip().upper()
    if instrument not in INSTRUMENTS:
        return _q("gateway", row_num, BAD_ENUM, f"unknown instrument {instrument!r}", row)

    status = str(row["status"]).strip().upper()
    if status not in PAYMENT_STATUSES:
        return _q("gateway", row_num, BAD_ENUM, f"unknown status {status!r}", row)

    fee, ferr = _optional_money(row, "fee_charged")
    if ferr:
        return _q("gateway", row_num, BAD_AMOUNT, ferr, row)
    gst, gerr = _optional_money(row, "gst_charged")
    if gerr:
        return _q("gateway", row_num, BAD_AMOUNT, gerr, row)
    tds, terr = _optional_money(row, "tds_withheld")
    if terr:
        return _q("gateway", row_num, BAD_AMOUNT, terr, row)

    intl = str(row.get("is_international", "false")).strip().lower() in ("1", "true", "yes", "y")

    return GatewayPayment(
        payment_id=str(row["payment_id"]).strip(),
        order_id=str(row["order_id"]).strip(),
        rrn=(str(row["rrn"]).strip() or None) if row.get("rrn") else None,
        captured_at=captured_at,
        amount=amount,
        instrument=instrument,
        bank=(str(row["bank"]).strip().upper() or None) if row.get("bank") else None,
        is_international=intl,
        status=status,
        fee_charged=fee,
        gst_charged=gst,
        tds_withheld=tds,
        row_num=row_num,
    )


def parse_bank_row(row: Dict[str, Any], row_num: int):
    for key in ("txn_id", "value_date", "amount", "direction"):
        val, err = _require(row, key)
        if err:
            return _q("bank", row_num, MISSING_FIELD, err, row)

    try:
        amount = Money.from_rupees_str(str(row["amount"]))
    except MoneyError as e:
        return _q("bank", row_num, BAD_AMOUNT, f"amount: {e}", row)

    try:
        value_date = parse_ist_date(str(row["value_date"]))
    except ClockError as e:
        return _q("bank", row_num, BAD_TIMESTAMP, f"value_date: {e}", row)

    direction = str(row["direction"]).strip().upper()
    if direction not in DIRECTIONS:
        return _q("bank", row_num, BAD_ENUM, f"unknown direction {direction!r}", row)

    return BankTransaction(
        txn_id=str(row["txn_id"]).strip(),
        value_date=value_date,
        amount=amount,
        direction=direction,
        utr=(str(row["utr"]).strip() or None) if row.get("utr") else None,
        narration=str(row.get("narration", "")).strip(),
        row_num=row_num,
    )


def parse_erp_row(row: Dict[str, Any], row_num: int):
    for key in ("invoice_id", "issued_date", "amount", "counterparty", "status"):
        val, err = _require(row, key)
        if err:
            return _q("erp", row_num, MISSING_FIELD, err, row)

    try:
        amount = Money.from_rupees_str(str(row["amount"]))
    except MoneyError as e:
        return _q("erp", row_num, BAD_AMOUNT, f"amount: {e}", row)

    try:
        issued_date = parse_ist_date(str(row["issued_date"]))
    except ClockError as e:
        return _q("erp", row_num, BAD_TIMESTAMP, f"issued_date: {e}", row)

    status = str(row["status"]).strip().upper()
    if status not in INVOICE_STATUSES:
        return _q("erp", row_num, BAD_ENUM, f"unknown status {status!r}", row)

    return ErpInvoice(
        invoice_id=str(row["invoice_id"]).strip(),
        issued_date=issued_date,
        amount=amount,
        counterparty=str(row["counterparty"]).strip(),
        status=status,
        row_num=row_num,
    )


_PARSERS = {"gateway": parse_gateway_row, "bank": parse_bank_row, "erp": parse_erp_row}


def ingest_rows(source: str, rows) -> IngestResult:
    """Parse an iterable of dict rows. Nothing is dropped; failures quarantine."""
    if source not in _PARSERS:
        raise ValueError(f"unknown source {source!r}")
    parser = _PARSERS[source]
    result = IngestResult()
    for i, row in enumerate(rows, start=1):
        parsed = parser(row, i)
        if isinstance(parsed, QuarantinedRow):
            result.quarantined.append(parsed)
        else:
            result.records.append(parsed)
    return result
