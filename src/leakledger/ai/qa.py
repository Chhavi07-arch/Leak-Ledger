"""Read-only question answering over a finished run.

There is no write path. This module is handed an already-computed summary and
returns prose; it cannot reach the cascade, the detectors or the ledger. That is
enforced by construction -- the function signature accepts a dict of facts, not
the engine -- rather than by a convention someone could later violate.
"""
from __future__ import annotations

import json
from typing import Any, Dict

SYSTEM = (
    "You answer questions about a completed reconciliation run using ONLY the "
    "summary JSON provided. If the summary does not contain the answer, say so "
    "plainly. Never estimate, never extrapolate, never invent a figure."
)


def ask(provider, *, summary: Dict[str, Any], question: str) -> str:
    reply = provider.complete(
        system=SYSTEM,
        prompt=f"Run summary:\n{json.dumps(summary, sort_keys=True, indent=2)}\n\n"
               f"Question: {question}",
        max_tokens=512)
    if reply.error:
        return f"(model unavailable: {reply.error})"
    return reply.text.strip()
