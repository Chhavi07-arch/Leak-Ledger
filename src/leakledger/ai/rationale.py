"""Plain-English rationale for an exception. Generation over settled facts only.

The model is handed a typed reason code and the arithmetic that produced it, and
asked to phrase it for a finance associate. It is given nothing to decide: the
verdict, the amounts and the next action are all computed before this is called.

The guard is that the rendered text must not introduce a number that is not
already in the evidence. A rationale that invents a figure is discarded and the
deterministic evidence string is shown instead -- the reader loses fluency, never
accuracy.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, Optional

SYSTEM = (
    "You write one or two plain sentences for a finance operations associate, "
    "explaining why a reconciliation item could not be resolved. "
    "Use ONLY the facts given. Never introduce a number that is not in the facts. "
    "No preamble, no apology, no speculation."
)

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text or "")}


def render(provider, *, reason_code: str, evidence: str,
           next_action: str) -> tuple[str, str]:
    """Return (text, provenance). Provenance is 'model' or 'deterministic'."""
    facts = (f"reason_code: {reason_code}\nevidence: {evidence}\n"
             f"next_action: {next_action}")
    reply = provider.complete(system=SYSTEM, prompt=facts, max_tokens=256)
    if reply.error or not reply.text.strip():
        return evidence, "deterministic"
    invented = _numbers(reply.text) - _numbers(facts)
    if invented:
        # A fabricated figure in a finance narrative is worse than no narrative.
        return evidence, "deterministic"
    return reply.text.strip(), "model"
