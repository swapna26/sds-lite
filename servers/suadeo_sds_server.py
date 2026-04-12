"""Suadeo SDS MCP Server — Doc §5.

Exposes the 15 Suadeo platform tools via the open Model Context Protocol
standard. In the Suadeo doc this server is written in C# .NET 10 with the
official Microsoft MCP SDK (§5.2). Here we use Python FastMCP so the
learning project stays single-language. Tool names, descriptions and
behaviours match the document 1:1.

Run standalone:  uv run servers/suadeo_sds_server.py

Tool families (Doc §5.3):
  §5.3.1 Catalogue & data        : search_catalogue, get_schema, execute_query,
                                    get_sample_data, get_lineage
  §5.3.2 ETL & dashboards        : create_pipeline, get_pipeline_status,
                                    create_dashboard, list_dashboards, run_profiling
  §5.3.3 Governance & documents  : get_governance, generate_word, generate_excel,
                                    generate_fake_data, get_user_context
"""

from __future__ import annotations

import json
import random
import string
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# Make sibling packages importable when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from assemblers.word_assembler import build_word_report  # noqa: E402
from assemblers.excel_assembler import build_excel_workbook  # noqa: E402
from assemblers.dashboard_assembler import validate_and_save_dashboard  # noqa: E402

mcp = FastMCP("Suadeo SDS")

MOCK_DIR = Path(__file__).parent.parent / "mock_data"
DATASETS = json.loads((MOCK_DIR / "datasets.json").read_text())["datasets"]
SCHEMAS = json.loads((MOCK_DIR / "schemas.json").read_text())
SAMPLES = json.loads((MOCK_DIR / "sample_rows.json").read_text())

# In-memory "platform state"
_PIPELINES: dict[str, dict] = {}
_DASHBOARDS: dict[str, dict] = {}
_PROFILING_JOBS: dict[str, dict] = {}


# =============================================================================
# §5.3.1 — Catalogue & data (5 tools)
# =============================================================================

@mcp.tool()
def search_catalogue(query: str, limit: int = 10) -> str:
    """Search datasets and assets in the Suadeo SDS catalogue.
    Returns name, type, description, key, owner."""
    q = (query or "").lower()
    matches = [
        d for d in DATASETS
        if q in d["name"].lower()
        or q in d["description"].lower()
        or q in d["key"].lower()
        or any(q in t for t in d.get("tags", []))
    ]
    if not matches:
        matches = DATASETS  # fallback — show everything
    return json.dumps({"results": matches[:limit], "count": len(matches[:limit])})


@mcp.tool()
def get_schema(key: str) -> str:
    """Full dataset schema: columns, data types, nullable, business description,
    sample values."""
    if key not in SCHEMAS:
        return json.dumps({"error": f"Dataset '{key}' not found",
                           "available": list(SCHEMAS.keys())})
    return json.dumps({"dataset_key": key, "columns": SCHEMAS[key]["columns"]})


@mcp.tool()
def execute_query(dataset_key: str, sql: str, limit: int = 100) -> str:
    """Secured SQL execution with user access control and automatic pagination
    (max 1000 rows)."""
    if dataset_key not in SAMPLES:
        return json.dumps({"error": f"Dataset '{dataset_key}' not found"})
    rows = SAMPLES[dataset_key][: min(limit, 1000)]
    return json.dumps({
        "dataset_key": dataset_key,
        "sql": sql,
        "rows": rows,
        "row_count": len(rows),
        "executed_at": datetime.now().isoformat(),
    })


@mcp.tool()
def get_sample_data(key: str, n: int = 5) -> str:
    """Returns N representative rows + per-column statistics
    (min, max, avg, cardinality)."""
    if key not in SAMPLES:
        return json.dumps({"error": f"Dataset '{key}' not found"})
    rows = SAMPLES[key][:n]
    stats: dict[str, dict[str, Any]] = {}
    if rows:
        for col in rows[0].keys():
            values = [r[col] for r in SAMPLES[key] if r.get(col) is not None]
            numeric = [v for v in values if isinstance(v, (int, float))]
            stat: dict[str, Any] = {"cardinality": len(set(map(str, values)))}
            if numeric:
                stat["min"] = min(numeric)
                stat["max"] = max(numeric)
                stat["avg"] = round(sum(numeric) / len(numeric), 2)
            stats[col] = stat
    return json.dumps({"dataset_key": key, "rows": rows, "stats": stats})


@mcp.tool()
def get_lineage(key: str) -> str:
    """Full dependency tree: upstream sources, transformations, downstream
    derived datasets."""
    lineage = {
        "dataset_key": key,
        "upstream": [
            {"key": f"raw_{key}", "type": "source", "system": "PostgreSQL"},
            {"key": f"staging_{key}", "type": "staging", "system": "S3"},
        ],
        "transformations": [
            {"step": 1, "name": "deduplicate", "type": "cleanse"},
            {"step": 2, "name": "enrich_with_geo", "type": "enrich"},
            {"step": 3, "name": "aggregate_monthly", "type": "aggregate"},
        ],
        "downstream": [
            {"key": f"{key}_dashboard", "type": "dashboard"},
            {"key": f"{key}_report", "type": "report"},
        ],
    }
    return json.dumps(lineage)


# =============================================================================
# §5.3.2 — ETL & dashboards (5 tools)
# =============================================================================

@mcp.tool()
def create_pipeline(config: dict) -> str:
    """Creates and deploys an ETL pipeline from a JSON config. Returns pipeline
    ID and initial status."""
    pipeline_id = f"pipe_{uuid.uuid4().hex[:8]}"
    _PIPELINES[pipeline_id] = {
        "id": pipeline_id,
        "config": config,
        "status": "running",
        "created_at": datetime.now().isoformat(),
        "logs": ["[INFO] Pipeline created", "[INFO] Initializing connectors"],
    }
    return json.dumps({"pipeline_id": pipeline_id, "status": "running"})


@mcp.tool()
def get_pipeline_status(pipeline_id: str) -> str:
    """Returns current state: running / success / failed + last 50 log lines."""
    if pipeline_id not in _PIPELINES:
        return json.dumps({"error": f"Pipeline '{pipeline_id}' not found"})
    p = _PIPELINES[pipeline_id]
    return json.dumps({
        "pipeline_id": pipeline_id,
        "status": p["status"],
        "logs": p["logs"][-50:],
    })


@mcp.tool()
def create_dashboard(dashboard: dict) -> str:
    """Creates or updates a DevExpress dashboard from a valid JSON definition.
    Returns dashboard URL."""
    try:
        path = validate_and_save_dashboard(dashboard)
    except Exception as e:
        return json.dumps({"error": str(e)})
    dashboard_id = dashboard["id"]
    _DASHBOARDS[dashboard_id] = dashboard
    return json.dumps({
        "dashboard_id": dashboard_id,
        "url": f"https://sds.suadeo.com/dashboards/{dashboard_id}",
        "file": path,
    })


@mcp.tool()
def list_dashboards(workspace_id: str = "default") -> str:
    """Lists dashboards in a workspace. Filters: workspace_id, owner, tags."""
    return json.dumps({
        "workspace_id": workspace_id,
        "dashboards": [
            {"id": did, "title": d.get("title", "")}
            for did, d in _DASHBOARDS.items()
        ],
    })


@mcp.tool()
def run_profiling(dataset_key: str) -> str:
    """Launches a data quality analysis on a dataset. Returns job_id.
    Results via get_profiling_result."""
    job_id = f"prof_{uuid.uuid4().hex[:8]}"
    _PROFILING_JOBS[job_id] = {
        "job_id": job_id,
        "dataset_key": dataset_key,
        "status": "completed",
        "quality_score": round(random.uniform(0.85, 0.99), 3),
        "null_ratio": round(random.uniform(0.0, 0.05), 3),
    }
    return json.dumps(_PROFILING_JOBS[job_id])


# =============================================================================
# §5.3.3 — Governance & document generation (5 tools)
# =============================================================================

@mcp.tool()
def get_governance(key: str) -> str:
    """Certification status, access rights, owners, and compliance rules for a
    given asset."""
    return json.dumps({
        "dataset_key": key,
        "certified": True,
        "certification_level": "gold",
        "owners": ["data.governance@suadeo.com"],
        "compliance": ["GDPR", "SOC2"],
        "access_rights": ["finance.team", "executive.board"],
    })


@mcp.tool()
def generate_word(content: dict) -> str:
    """Generates a structured .docx report from a JSON content definition
    (DocumentFormat.OpenXml → python-docx in this lite version)."""
    file_path = build_word_report(content)
    return json.dumps({"file_path": file_path, "format": "docx"})


@mcp.tool()
def generate_excel(content: dict) -> str:
    """Generates an .xlsx workbook with data, formulas, and charts
    (ClosedXML → openpyxl in this lite version)."""
    file_path = build_excel_workbook(content)
    return json.dumps({"file_path": file_path, "format": "xlsx"})


@mcp.tool()
def generate_fake_data(schema: dict, n: int = 10) -> str:
    """Generates a typed test dataset from a provided schema. Supported types:
    string, int, decimal, date, email, phone, address, uuid."""
    rows = []
    columns = schema.get("columns", [])
    for _ in range(n):
        row = {}
        for col in columns:
            row[col["name"]] = _fake_value(col.get("type", "string"))
        rows.append(row)
    return json.dumps({"rows": rows, "count": len(rows)})


@mcp.tool()
def get_user_context() -> str:
    """Returns the current user's full profile: roles, accessible workspaces,
    per-dataset rights."""
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
    })


# =============================================================================
# Fake data generator helpers
# =============================================================================

def _fake_value(col_type: str):
    t = col_type.lower()
    if t in ("string", "text"):
        return "".join(random.choices(string.ascii_letters, k=8))
    if t in ("int", "integer"):
        return random.randint(1, 1000)
    if t in ("decimal", "number", "float"):
        return round(random.uniform(0, 10000), 2)
    if t == "date":
        return (datetime(2026, 1, 1) + timedelta(days=random.randint(0, 365))).date().isoformat()
    if t == "email":
        user = "".join(random.choices(string.ascii_lowercase, k=6))
        return f"{user}@example.com"
    if t == "phone":
        return f"+33 {random.randint(100000000, 999999999)}"
    if t == "address":
        return f"{random.randint(1, 99)} Rue de Paris, 75001 Paris"
    if t == "uuid":
        return str(uuid.uuid4())
    return "value"


if __name__ == "__main__":
    mcp.run(transport="stdio")
