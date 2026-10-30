"""Live web search for agent turns, served by the Tavily Search API.

One POST to https://api.tavily.com/search per tool call, authenticated with
``Authorization: Bearer <TAVILY_API_KEY>``. The tool is offered to agents only
when the key is set; without it the model never sees ``web_search``.

API reference: https://docs.tavily.com/documentation/api-reference/endpoint/search
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import get_settings

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
MAX_RESULTS = 5
SNIPPET_CHARS = 500


class WebSearchError(RuntimeError):
    """Tavily refused or failed the request; the message is safe to show."""


@dataclass(frozen=True)
class WebSearchResult:
    query: str
    answer: str
    results: list[dict]
    request_id: str
    response_time: float | None


def web_search_configured() -> bool:
    return bool(get_settings().TAVILY_API_KEY)


async def tavily_search(query: str, *, topic: str = "general", max_results: int = MAX_RESULTS) -> WebSearchResult:
    """Run one basic-depth Tavily search (1 credit) and return ranked sources."""
    key = get_settings().TAVILY_API_KEY
    if not key:
        raise WebSearchError("Web search is not configured. Set TAVILY_API_KEY.")
    payload = {
        "query": query[:400],
        "topic": topic if topic in {"general", "news", "finance"} else "general",
        "search_depth": "basic",
        "max_results": max(1, min(int(max_results), MAX_RESULTS)),
        "include_answer": "basic",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                TAVILY_SEARCH_URL,
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
    except httpx.HTTPError as exc:
        raise WebSearchError("Web search is unreachable right now.") from exc
    if response.status_code != 200:
        raise WebSearchError(f"Web search failed with HTTP {response.status_code}.")
    data = response.json()
    results = [
        {
            "title": str(item.get("title") or item.get("url") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("content") or "")[:SNIPPET_CHARS],
            "score": item.get("score"),
            "published_date": item.get("published_date"),
        }
        for item in data.get("results") or []
        if item.get("url")
    ]
    return WebSearchResult(
        query=str(data.get("query") or query),
        answer=str(data.get("answer") or ""),
        results=results,
        request_id=str(data.get("request_id") or ""),
        response_time=data.get("response_time"),
    )
