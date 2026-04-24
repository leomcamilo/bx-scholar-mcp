"""Unified search tool — queries multiple sources in parallel with dedup."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from bx_scholar_core.clients.arxiv import ArXivClient
from bx_scholar_core.clients.crossref import CrossRefClient
from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.clients.scielo import SciELOClient
from bx_scholar_core.clients.semantic_scholar import SemanticScholarClient
from bx_scholar_core.clients.tavily import TavilyClient
from bx_scholar_core.config import Settings
from bx_scholar_core.dedup import deduplicate
from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.paper import Paper

if TYPE_CHECKING:
    from bx_scholar_core.cache import CacheStore

logger = get_logger(__name__)


def _papers_to_json(papers: list[Paper], total: int = 0, meta: dict | None = None) -> str:
    """Serialize papers list to JSON string for MCP response."""
    result = {
        "total_results": total or len(papers),
        "returned": len(papers),
        "results": [p.model_dump(exclude_defaults=True) for p in papers],
    }
    if meta:
        result.update(meta)
    return json.dumps(result, ensure_ascii=False, indent=2)


def register_search_tools(mcp: object, settings: Settings, cache: CacheStore | None = None) -> None:
    """Register search-related tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]

    @server.tool()
    async def search_papers(
        query: str,
        sources: str = "openalex,crossref",
        year_from: int | None = None,
        year_to: int | None = None,
        journal_issn: str | None = None,
        sort: str = "cited_by_count:desc",
        per_page: int = 25,
    ) -> str:
        """Search academic papers across multiple sources with automatic deduplication.
        sources: comma-separated list from openalex, crossref, arxiv, scielo, semantic_scholar, tavily.
        ArXiv results are always marked as grey literature (not peer-reviewed)."""
        source_list = [s.strip().lower() for s in sources.split(",")]
        all_papers: list[Paper] = []
        total = 0
        source_counts: dict[str, int] = {}

        async def _search_openalex() -> None:
            nonlocal total
            client = OpenAlexClient(settings.polite_email, settings.user_agent, cache=cache)
            try:
                papers, count = await client.search(
                    query, year_from, year_to, journal_issn, sort=sort, per_page=per_page
                )
                all_papers.extend(papers)
                total += count
                source_counts["openalex"] = len(papers)
            finally:
                await client.close()

        async def _search_crossref() -> None:
            nonlocal total
            client = CrossRefClient(settings.polite_email, settings.user_agent, cache=cache)
            try:
                papers, count = await client.search(query, year_from, year_to, rows=per_page)
                all_papers.extend(papers)
                total += count
                source_counts["crossref"] = len(papers)
            finally:
                await client.close()

        async def _search_arxiv() -> None:
            client = ArXivClient(user_agent=settings.user_agent, cache=cache)
            try:
                papers = await client.search(query, max_results=min(per_page, 20))
                all_papers.extend(papers)
                source_counts["arxiv"] = len(papers)
            finally:
                await client.close()

        async def _search_scielo() -> None:
            client = SciELOClient(settings.polite_email, settings.user_agent, cache=cache)
            try:
                papers = await client.search(query, year_from, year_to, max_results=per_page)
                all_papers.extend(papers)
                source_counts["scielo"] = len(papers)
            finally:
                await client.close()

        async def _search_s2() -> None:
            nonlocal total
            year_str = None
            if year_from and year_to:
                year_str = f"{year_from}-{year_to}"
            elif year_from:
                year_str = f"{year_from}-"
            client = SemanticScholarClient(settings.s2_api_key, settings.user_agent, cache=cache)
            try:
                papers, count = await client.search(query, year=year_str, limit=per_page)
                all_papers.extend(papers)
                total += count
                source_counts["semantic_scholar"] = len(papers)
            finally:
                await client.close()

        async def _search_tavily() -> None:
            if not settings.tavily_api_key:
                return
            client = TavilyClient(settings.tavily_api_key, settings.user_agent, cache=cache)
            try:
                results = await client.search(query, max_results=min(per_page, 10))
                source_counts["tavily"] = len(results)
                # Tavily returns dicts, not Papers — include as-is in meta
            finally:
                await client.close()

        tasks_map = {
            "openalex": _search_openalex,
            "crossref": _search_crossref,
            "arxiv": _search_arxiv,
            "scielo": _search_scielo,
            "semantic_scholar": _search_s2,
            "tavily": _search_tavily,
        }

        tasks = [tasks_map[s]() for s in source_list if s in tasks_map]
        await asyncio.gather(*tasks, return_exceptions=True)

        deduped = deduplicate(all_papers)
        logger.info(
            "search_complete",
            query=query,
            sources=source_list,
            raw=len(all_papers),
            deduped=len(deduped),
        )
        return _papers_to_json(
            deduped,
            total=total,
            meta={
                "sources_queried": source_list,
                "per_source": source_counts,
                "duplicates_removed": len(all_papers) - len(deduped),
            },
        )

    @server.tool()
    async def search_journal_papers(
        issn: str,
        query: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        per_page: int = 25,
    ) -> str:
        """Search papers within a specific journal by ISSN.
        Essential for finding papers from the target journal for calibration."""
        client = OpenAlexClient(settings.polite_email, settings.user_agent)
        try:
            papers, total = await client.search(
                query or "",
                year_from=year_from,
                year_to=year_to,
                journal_issn=issn,
                per_page=per_page,
            )
            return _papers_to_json(papers, total, meta={"journal_issn": issn})
        finally:
            await client.close()
