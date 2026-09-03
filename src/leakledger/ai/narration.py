"""Bank narration -> candidate entities. The model PROPOSES; arithmetic DISPOSES.

This is the single place a model's output touches the matching pipeline, and it
is deliberately shaped so that it cannot decide anything. `propose()` returns
CANDIDATES. `verify()` accepts a candidate only if the deterministic record it
names actually exists AND its amount agrees exactly. A proposal that fails
verification is discarded and the record falls through to the next tier -- the
model gets no second chance and no partial credit.

The asymmetry is the point: the model can only ever REDUCE the search space, and
only in ways arithmetic independently confirms. It cannot introduce a match, it
cannot break a tie, and it cannot override a refusal.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..money import Money

SYSTEM = (
    "You extract structured fields from Indian bank statement narration lines. "
    "Reply with ONLY a JSON object: "
    '{"counterparty": string, "reference": string, "confidence": number}. '
    "Use the empty string when a field is not present. Do not explain."
)


@dataclass(frozen=True)
class Candidate:
    counterparty: str
    reference: str
    confidence: float
    source: str = "model"


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reason: str
    candidate: Optional[Candidate] = None
    matched_record_id: Optional[str] = None


def parse_reply(text: str) -> Optional[Candidate]:
    """Parse a model reply. Malformed output is rejected, never repaired."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict):
        return None
    try:
        conf = float(d.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return Candidate(counterparty=str(d.get("counterparty", "")).strip(),
                     reference=str(d.get("reference", "")).strip(),
                     confidence=conf)


def propose(provider, narration: str) -> Optional[Candidate]:
    reply = provider.complete(system=SYSTEM, prompt=f"Narration: {narration}")
    if reply.error:
        return None
    return parse_reply(reply.text)


def verify(candidate: Optional[Candidate], *, expected_amount: Money,
           records_by_reference: Dict[str, Money]) -> Verdict:
    """Accept only if the named reference exists AND the amount agrees exactly.

    No tolerance band. Money is integer paise; an exact match is the honest test,
    and a tolerance here would let a confident wrong proposal through on a near
    miss -- which is exactly the failure mode this function exists to prevent.
    """
    if candidate is None:
        return Verdict(False, "no parseable proposal")
    if not candidate.reference:
        return Verdict(False, "proposal names no reference", candidate)
    actual = records_by_reference.get(candidate.reference)
    if actual is None:
        return Verdict(False,
                       f"proposed reference {candidate.reference!r} does not exist "
                       f"in the deterministic record set", candidate)
    if actual.paise != expected_amount.paise:
        return Verdict(False,
                       f"proposed reference {candidate.reference!r} exists but its amount "
                       f"{actual} != {expected_amount}; arithmetic rejects the proposal",
                       candidate)
    # Confidence is NOT consulted. A high-confidence wrong answer and a
    # low-confidence right answer are treated identically, because the model's
    # own estimate of its correctness is not evidence about the world.
    return Verdict(True, "reference exists and amount agrees exactly",
                   candidate, candidate.reference)
