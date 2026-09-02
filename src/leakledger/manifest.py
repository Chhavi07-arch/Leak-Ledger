"""Run manifest: what code, what config, what inputs produced these numbers.

Without this, a reported metric is an assertion. With it, the metric is
reproducible against the exact contract and the exact inputs it was computed
over. The manifest is written for every run and is what makes the determinism
claim (5 runs, one hash) meaningful rather than decorative.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .clock import IST


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def _git_dirty() -> Optional[bool]:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(out.stdout.strip()) if out.returncode == 0 else None
    except Exception:
        return None


@dataclass
class RunManifest:
    run_id: str
    started_at: str
    git_commit: Optional[str]
    git_dirty: Optional[bool]
    config_hashes: Dict[str, str] = field(default_factory=dict)
    input_hashes: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @classmethod
    def start(cls, *, config_paths=(), input_paths=(), run_id: Optional[str] = None) -> "RunManifest":
        return cls(
            run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
            started_at=datetime.now(IST).isoformat(),
            git_commit=git_commit(),
            git_dirty=_git_dirty(),
            config_hashes={str(p): file_sha256(p) for p in config_paths},
            input_hashes={str(p): file_sha256(p) for p in input_paths},
        )

    def note(self, text: str) -> None:
        self.notes.append(text)

    def write(self, path: Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return p
