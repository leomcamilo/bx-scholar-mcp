"""Unified search tool — queries multiple sources in parallel with dedup."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from bx_scholar_core.clients.vufind import THESIS_FORMATS
from bx_scholar_core.dedup import deduplicate
from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.paper import Paper
from bx_scholar_core.sources import PRESETS, SEARCH_SOURCES, SearchQuery, expand_sources

if TYPE_CHECKING:
    from bx_scholar_core.clients.pool import ClientPool

logger = get_logger(__name__)

THESIS_SCOPES = {"br": ("bdtd",), "latam": ("bdtd", "lareferencia")}
THESIS_DEGREES = {
    "all": THESIS_FORMATS,
    "master": ("masterThesis",),
    "doctoral": ("doctoralThesis",),
}


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


async def run_search(
    pool: ClientPool, source_names: list[str], query: SearchQuery
) -> tuple[list[Paper], int, dict[str, object]]:
    """Query the named sources concurrently. One failing source never fails the rest;
    its error is reported in meta["errors"] instead of being swallowed."""
    runnable = []
    skipped: dict[str, str] = {}
    for name in source_names:
        source = SEARCH_SOURCES[name]
        reason = source.missing_config(pool)
        if reason:
            skipped[name] = reason
        else:
            runnable.append(source)

    results = await asyncio.gather(*(s.run(pool, query) for s in runnable), return_exceptions=True)

    papers: list[Paper] = []
    total = 0
    per_source: dict[str, int] = {}
    errors: dict[str, str] = {}
    for source, result in zip(runnable, results, strict=True):
        if isinstance(result, BaseException):
            errors[source.name] = f"{type(result).__name__}: {result}"
            logger.warning("search_source_failed", source=source.name, error=str(result))
            continue
        found, count = result
        papers.extend(found)
        total += count
        per_source[source.name] = len(found)

    deduped = deduplicate(papers)
    meta: dict[str, object] = {
        "per_source": per_source,
        "duplicates_removed": len(papers) - len(deduped),
    }
    if errors:
        meta["errors"] = errors
    if skipped:
        meta["skipped"] = skipped
    return deduped, total, meta


def register_search_tools(mcp: object, pool: ClientPool) -> None:
    """Register search-related tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]
    presets = "; ".join(f"{k}={'+'.join(v)}" for k, v in PRESETS.items())
    search_papers_doc = (
        "Search academic papers across multiple sources with automatic deduplication.\n"
        f"sources: comma-separated list from {', '.join(SEARCH_SOURCES)}, "
        f"or a preset ({presets}).\n"
        "ArXiv results are always marked as grey literature (not peer-reviewed). "
        'A source that fails or lacks its API key is listed under "errors" or "skipped" '
        "instead of silently returning nothing."
    )

    @server.tool(structured_output=False, description=search_papers_doc)
    async def search_papers(
        query: str,
        sources: str = "openalex,crossref",
        year_from: int | None = None,
        year_to: int | None = None,
        journal_issn: str | None = None,
        sort: str = "cited_by_count:desc",
        per_page: int = 25,
    ) -> str:
        names, unknown = expand_sources(sources)
        papers, total, meta = await run_search(
            pool,
            names,
            SearchQuery(query, year_from, year_to, per_page, journal_issn, sort),
        )
        logger.info("search_complete", query=query, sources=names, returned=len(papers), **meta)
        meta = {"sources_queried": names, **meta}
        if unknown:
            meta["unknown_sources"] = unknown
        return _papers_to_json(papers, total=total, meta=meta)

    @server.tool(structured_output=False)
    async def search_theses(
        query: str,
        scope: str = "br",
        degree: str = "all",
        year_from: int | None = None,
        year_to: int | None = None,
        per_page: int = 25,
    ) -> str:
        """Search master's and doctoral theses (teses e dissertações), which OpenAlex
        barely covers for Latin America.
        scope: 'br' = BDTD (Brazil, IBICT); 'latam' = BDTD + LA Referencia (Latin America).
        degree: 'all', 'master' or 'doctoral'.
        Each result carries institution, format and, when the portal has them, the
        advisors (external_ids.advisors). Records rarely have a DOI: cite via landing_url."""
        sources = THESIS_SCOPES.get(scope)
        formats = THESIS_DEGREES.get(degree)
        if sources is None or formats is None:
            return json.dumps(
                {
                    "error": "scope must be one of "
                    f"{sorted(THESIS_SCOPES)} and degree one of {sorted(THESIS_DEGREES)}"
                }
            )
        papers, total, meta = await run_search(
            pool,
            list(sources),
            SearchQuery(query, year_from, year_to, per_page, formats=formats),
        )
        return _papers_to_json(papers, total=total, meta={"scope": scope, "degree": degree, **meta})

    @server.tool(structured_output=False)
    async def search_journal_papers(
        issn: str,
        query: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        per_page: int = 25,
    ) -> str:
        """Search papers within a specific journal by ISSN.
        Essential for finding papers from the target journal for calibration."""
        papers, total = await pool.openalex.search(
            query or "",
            year_from=year_from,
            year_to=year_to,
            journal_issn=issn,
            per_page=per_page,
        )
        return _papers_to_json(papers, total, meta={"journal_issn": issn})
