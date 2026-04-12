"""Audit Trail — Step ⑨ (Doc §8.2).

Every agent invocation must log:
    skill_used, tools_called, user_id, latency_ms, token_count

We serialise each invocation as a JSONL line under outputs/audit.log.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

AUDIT_LOG = Path(__file__).parent.parent / "outputs" / "audit.log"


@dataclass
class AuditTrail:
    skill_used: str
    tools_called: list[str] = field(default_factory=list)
    user_id: str = "u_demo_001"
    latency_ms: int = 0
    token_count: int = 0
    classifier_score: float = 0.0
    fallback_used: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_audit(trail: AuditTrail) -> None:
    """Append a JSONL line to outputs/audit.log."""
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    trail.timestamp = datetime.now().isoformat()
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(trail.to_dict()) + "\n")
