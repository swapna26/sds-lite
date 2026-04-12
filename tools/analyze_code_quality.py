"""analyze_code_quality — Doc §4.2 agent tool.

Sends a GitLab diff to the LLM with a specialised prompt. Returns a structured
quality analysis. Here we return a deterministic heuristic analysis so the
function is self-contained (the LLM can of course still reason over the
returned structure).
"""

from __future__ import annotations

import json
import re


_SECURITY_PATTERNS = [
    (r"(?i)password\s*=\s*['\"]", "Hardcoded password detected"),
    (r"(?i)f['\"].*WHERE.*\{.*\}", "Possible SQL injection via f-string"),
    (r"(?i)eval\s*\(", "Use of eval() — remote code execution risk"),
    (r"(?i)shell\s*=\s*True", "subprocess with shell=True"),
]


def analyze_code_quality(diff: str, language: str = "python") -> str:
    """Run a lightweight static analysis over a unified diff."""
    if not diff:
        return json.dumps({"error": "empty diff"})

    added_lines = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    removed_lines = [l for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]

    issues = []
    for pattern, message in _SECURITY_PATTERNS:
        for line in added_lines:
            if re.search(pattern, line):
                issues.append({"severity": "high", "category": "security", "message": message, "line": line.strip()})

    # Heuristic flags
    for line in added_lines:
        if "TODO" in line or "FIXME" in line:
            issues.append({"severity": "low", "category": "tech_debt", "message": "TODO/FIXME comment added", "line": line.strip()})
        if len(line) > 120:
            issues.append({"severity": "info", "category": "style", "message": "Line exceeds 120 chars", "line": line.strip()[:100]})

    score = max(0.0, 1.0 - 0.15 * sum(1 for i in issues if i["severity"] == "high")
                         - 0.05 * sum(1 for i in issues if i["severity"] == "low"))

    return json.dumps({
        "language": language,
        "added_line_count": len(added_lines),
        "removed_line_count": len(removed_lines),
        "quality_score": round(score, 2),
        "issues": issues,
    })
