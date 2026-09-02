"""Append-only audit log.

Every decision the engine makes is written here with the evidence that produced
it: which record, which stage, what was decided, under which reason code and
tier, and the hash of the inputs the decision was taken over.

Append-only is enforced, not merely intended. The writer opens in append mode
and refuses to truncate; a caller that wants to start fresh must delete the file
deliberately. This is what makes an auto-applied match reversible, which is the
actual reason a human is allowed to skip reviewing one (PLAN.md, "Why an
auto-applied match can go unreviewed").
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from .clock import IST


class AuditError(RuntimeError):
    """Raised on any attempt to rewrite history."""


def evidence_hash(payload: Any) -> str:
    """Stable hash of a decision's inputs. Key order never affects the digest."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    ts: str
    run_id: str
    stage: str
    record_id: str
    decision: str
    reason_code: Optional[str]
    tier: Optional[str]
    evidence_sha256: str
    detail: Dict[str, Any] = field(default_factory=dict)


class AuditLog:
    """JSONL append-only writer. One line per decision, ordered by seq."""

    def __init__(self, path: Path, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = self._existing_max_seq()

    def _existing_max_seq(self) -> int:
        if not self.path.exists():
            return 0
        last = 0
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        last = max(last, int(json.loads(line)["seq"]))
                    except (ValueError, KeyError):
                        raise AuditError(f"corrupt audit line in {self.path}: {line[:80]!r}")
        return last

    def append(
        self,
        *,
        stage: str,
        record_id: str,
        decision: str,
        reason_code: Optional[str] = None,
        tier: Optional[str] = None,
        evidence: Any = None,
        **detail: Any,
    ) -> AuditEntry:
        self._seq += 1
        entry = AuditEntry(
            seq=self._seq,
            ts=datetime.now(IST).isoformat(),
            run_id=self.run_id,
            stage=stage,
            record_id=record_id,
            decision=decision,
            reason_code=reason_code,
            tier=tier,
            evidence_sha256=evidence_hash(evidence if evidence is not None else {}),
            detail=detail,
        )
        # "a" never truncates; the O_APPEND write is atomic for small lines.
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), separators=(",", ":"), default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry

    def read_all(self) -> Iterator[AuditEntry]:
        if not self.path.exists():
            return iter(())
        entries = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(AuditEntry(**json.loads(line)))
        return iter(entries)

    def __len__(self) -> int:
        return self._seq
