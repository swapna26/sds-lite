"""Mock GitLab MCP Server — for Doc §7.2 daily code analysis use case.

In production this would be the real Atlassian/GitLab MCP servers referenced
in Doc §6.3. Here we expose a tiny mock so the suadeo-code-review skill runs
end-to-end during learning.

Run standalone:  uv run servers/gitlab_server.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Mock GitLab")

# --- Fake commit history ---
_BASE = datetime.now() - timedelta(hours=6)
_COMMITS = [
    {
        "sha": "a1b2c3d",
        "author": "alice@suadeo.com",
        "message": "feat(auth): add OAuth2 device flow",
        "project": "sds-core",
        "created_at": (_BASE + timedelta(hours=1)).isoformat(),
    },
    {
        "sha": "e4f5g6h",
        "author": "bob@suadeo.com",
        "message": "fix(query): prevent SQL injection in dataset filter",
        "project": "sds-core",
        "created_at": (_BASE + timedelta(hours=2)).isoformat(),
    },
    {
        "sha": "i7j8k9l",
        "author": "carol@suadeo.com",
        "message": "refactor(dashboard): extract chart rendering helper",
        "project": "sds-frontend",
        "created_at": (_BASE + timedelta(hours=3)).isoformat(),
    },
]

_DIFFS = {
    "a1b2c3d": """--- a/src/auth/oauth.py
+++ b/src/auth/oauth.py
@@ -10,3 +10,18 @@ def get_token(code: str) -> str:
     return _fetch_token(code)

+def device_flow_start(client_id: str) -> dict:
+    \"\"\"Initiate RFC 8628 device authorization flow.\"\"\"
+    resp = httpx.post(AUTH_URL + '/device_authorization',
+                      data={'client_id': client_id})
+    return resp.json()
""",
    "e4f5g6h": """--- a/src/query/filter.py
+++ b/src/query/filter.py
@@ -5,3 +5,6 @@ def build_filter(column: str, value: str) -> str:
-    return f\"WHERE {column} = '{value}'\"
+    # SECURITY: use parameterised query instead of string concat
+    if not column.isidentifier():
+        raise ValueError('invalid column')
+    return ('WHERE {} = %s'.format(column), [value])
""",
    "i7j8k9l": """--- a/src/frontend/dashboard.tsx
+++ b/src/frontend/dashboard.tsx
@@ -22,10 +22,5 @@ function Dashboard() {
-  const renderChart = (data) => {
-    return <Chart data={data} options={defaultOptions} />;
-  };
-  return <div>{items.map(renderChart)}</div>;
+  return <div>{items.map(d => <ChartItem data={d} />)}</div>;
""",
}


@mcp.tool()
def get_commits(hours: int = 24) -> str:
    """Return commits pushed in the last N hours across all projects."""
    cutoff = datetime.now() - timedelta(hours=hours)
    recent = [c for c in _COMMITS if datetime.fromisoformat(c["created_at"]) >= cutoff]
    return json.dumps({"commits": recent, "count": len(recent)})


@mcp.tool()
def get_diff(sha: str) -> str:
    """Return the unified diff for a given commit SHA."""
    if sha not in _DIFFS:
        return json.dumps({"error": f"Unknown commit: {sha}"})
    return json.dumps({"sha": sha, "diff": _DIFFS[sha]})


@mcp.tool()
def list_projects() -> str:
    """List active GitLab projects."""
    projects = sorted({c["project"] for c in _COMMITS})
    return json.dumps({"projects": projects})


if __name__ == "__main__":
    mcp.run(transport="stdio")
