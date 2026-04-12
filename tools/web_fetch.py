"""web_fetch — Doc §4.2 agent tool.

Retrieves the full content of a URL. Used after web_search to read a full
article or page.
"""

from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger(__name__)


async def web_fetch(url: str, max_chars: int = 10_000) -> str:
    """Fetch the text content of a URL (truncated to max_chars)."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "sds-lite/0.1"})
            r.raise_for_status()
        text = r.text[:max_chars]
        return json.dumps({"url": url, "status": r.status_code, "content": text})
    except Exception as e:
        logger.warning("web_fetch failed: %s", e)
        return json.dumps({"url": url, "error": str(e)})
