"""web_search — Doc §4.2 agent tool.

Real-time web search. The architecture doc mentions SerpAPI / Brave Search. For
learning we use DuckDuckGo which requires no API key and matches the pattern
already used in langgraph-mcp-agent/servers/web_rag_server.py.
"""

from __future__ import annotations

import json
import logging

from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)


def web_search(query: str, top_k: int = 5) -> str:
    """Real-time web search via DuckDuckGo. Returns top K results with
    title, URL, and snippet."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=top_k))
        formatted = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in results
        ]
        return json.dumps({"query": query, "results": formatted})
    except Exception as e:
        logger.warning("web_search failed: %s", e)
        return json.dumps({"query": query, "results": [], "error": str(e)})
