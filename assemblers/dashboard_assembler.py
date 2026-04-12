"""Dashboard Assembler — Step ⑧ for DevExpress-style dashboard JSON.

Validates structure and persists the JSON under outputs/. Mirrors the
suadeo.create_dashboard contract from Doc §5.3.2.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

OUTPUT_DIR = Path(__file__).parent.parent / "outputs"


class DashboardValidationError(ValueError):
    pass


def validate_and_save_dashboard(dashboard: dict[str, Any]) -> str:
    """Minimal DevExpress-style validation + persistence."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not isinstance(dashboard, dict):
        raise DashboardValidationError("Dashboard must be a JSON object")
    if "title" not in dashboard:
        raise DashboardValidationError("Dashboard missing required field: title")
    if "items" not in dashboard or not isinstance(dashboard["items"], list):
        raise DashboardValidationError("Dashboard missing required field: items (list)")
    if not dashboard["items"]:
        raise DashboardValidationError("Dashboard must contain at least one item")

    # Assign an id if missing
    dashboard.setdefault("id", f"dashboard_{uuid.uuid4().hex[:8]}")

    filename = f"{dashboard['id']}.json"
    out_path = OUTPUT_DIR / filename
    out_path.write_text(json.dumps(dashboard, indent=2))
    return str(out_path.relative_to(OUTPUT_DIR.parent))
