"""Tavily API client — web search for reports and policy documents."""

from __future__ import annotations

from typing import Any

from bx_scholar_core.clients.base import AsyncHTTPClient


class TavilyClient(AsyncHTTPClient):
    """Client for the Tavily Search API.

    Rate limit: 5 req/s (conservative).
    """

    base_url = "https://api.tavily.com"
    rate_limit = 5.0
    max_rate_period = 1.0

    def __init__(self, api_key: str, user_agent: str = "", **kwargs) -> None:
        super().__init__(user_agent=user_agent or "BX-Scholar/0.1.0", **kwargs)
        self._api_key = api_key

    async def search(
        self,
        query: str,
        search_depth: str = "basic",
        include_domains: list[str] | None = None,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Search the web. Returns list of results with title, url, content, score."""
        if not self._api_key:
            return []

        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": search_depth,
            "max_results": min(max_results, 20),
        }
        if include_domains:
            payload["include_domains"] = include_domains

        resp = await self.post("/search", json=payload, cache_policy=("web_search", 3600))
        data = resp.json()

        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:300],
                "score": r.get("score", 0),
                "source_type": "web_search",
            }
            for r in data.get("results", [])
        ]
