"""generate_chart — Doc §4.2 agent tool.

Generates Plotly/Chart.js JSON from tabular data. Rendered client-side in the
Platform frontend. Here we return a minimal Plotly-compatible JSON structure.
"""

from __future__ import annotations

import json
from typing import Any


def generate_chart(
    chart_type: str,
    x: list[Any],
    y: list[Any],
    title: str = "",
    x_label: str = "",
    y_label: str = "",
) -> str:
    """Return a Plotly figure JSON for the given data.

    chart_type: 'bar' | 'line' | 'scatter' | 'pie'
    """
    ct = chart_type.lower()
    if ct not in {"bar", "line", "scatter", "pie"}:
        return json.dumps({"error": f"Unsupported chart type: {chart_type}"})

    if ct == "pie":
        data = [{"type": "pie", "labels": x, "values": y}]
    else:
        trace_type = {"bar": "bar", "line": "scatter", "scatter": "scatter"}[ct]
        mode = "lines+markers" if ct == "line" else ("markers" if ct == "scatter" else None)
        trace: dict[str, Any] = {"type": trace_type, "x": x, "y": y}
        if mode:
            trace["mode"] = mode
        data = [trace]

    layout = {"title": title}
    if x_label:
        layout["xaxis"] = {"title": x_label}
    if y_label:
        layout["yaxis"] = {"title": y_label}

    return json.dumps({"data": data, "layout": layout})
