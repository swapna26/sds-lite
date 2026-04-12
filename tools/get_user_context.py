"""get_user_context — Doc §4.2 agent tool.

Returns the current user's profile, roles, and dataset-level access rights
from the Suadeo session. Mock implementation for the learning project.

Note: There is also a MCP-server version of this tool (suadeo.get_user_context)
in Doc §5.3.3 — they are intentionally duplicated in the document. The
agent-tool version is for in-process session context; the MCP version is for
remote clients.
"""

from __future__ import annotations

import json


def get_user_context() -> str:
    """Return the current user's profile and access rights."""
    return json.dumps({
        "user_id": "u_demo_001",
        "name": "Demo User",
        "email": "demo.user@suadeo.com",
        "roles": ["analyst", "report_author"],
        "workspaces": ["finance", "hr"],
        "dataset_rights": {
            "sales_q1_2026": ["read", "query"],
            "hr_headcount": ["read"],
            "customer_list": ["read", "query"],
        },
        "preferences": {
            "language": "en",
            "locale": "en-GB",
        },
    })
