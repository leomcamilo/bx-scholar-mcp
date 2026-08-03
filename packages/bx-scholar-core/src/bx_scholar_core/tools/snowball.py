"""Snowballing tools — iterative literature expansion for systematic reviews.

Implements Wohlin-style snowballing: from seed papers, expand backward
(references) and/or forward (citing papers) for one or more iterations,
with global deduplication and abstracts included.

Also provides resolve_reference_list: turn a raw bibliography (e.g. the
References section of a PDF extracted with extract_pdf_text) into verified
papers with DOIs and abstracts — one CrossRef bibliographic lookup per entry.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from bx_scholar_core.config import Settings
from bx_scholar_core.dedup import deduplicate
from bx_scholar_core.id_resolver import resolve_id
from bx_scholar_core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bx_scholar_core.cache import CacheStore
    from bx_scholar_core.models.paper import Paper

logger = get_logger(__name__)

# Caps to keep one tool call bounded (politeness + response size)
MAX_FRONTIER_PER_LEVEL = 25
PER_NODE_RESULTS = 50
MAX_REFERENCE_LINES = 50
TITLE_MATCH_THRESHOLD = 80.0

_LINE_PREFIX = re.compile(r"^\s*(?:\[\d+\]|\d+[.)]|[•*-])\s*")


def _paper_key(paper: Paper) -> str:
    """Stable identity for seen-set: DOI first, then OpenAlex ID, then title."""
    if paper.doi:
        return f"doi:{paper.doi.lower()}"
    if paper.openalex_id:
        return f"oa:{paper.openalex_id}"
    return f"title:{paper.title.lower().strip()}"


def clean_reference_lines(text: str) -> list[str]:
    """Split raw bibliography text into one cleaned reference string per line.

    Strips numbering prefixes ([1], 1., 1), bullets) and drops lines too short
    to be a bibliographic entry.
    """
    lines = []
    for raw in text.splitlines():
        line = _LINE_PREFIX.sub("", raw).strip()
        if len(line) >= 25:
            lines.append(line)
    return lines


async def snowball_bfs(
    fetch: Callable[[str, str], Awaitable[list[Paper]]],
    seed_dois: list[str],
    directions: list[str],
    max_depth: int,
    max_papers: int,
    min_cited_by: int,
    year_from: int | None,
) -> tuple[list[Paper], list[dict], list[dict]]:
    """Breadth-first snowball expansion. Pure orchestration — fetch is injected.

    fetch(doi, direction) must return list[Paper]; direction is
    "references" or "citing". Returns (papers, edges, level_stats).
    """
    seen: set[str] = {f"doi:{d.lower()}" for d in seed_dois}
    collected: list[Paper] = []
    edges: list[dict] = []
    level_stats: list[dict] = []
    frontier = list(seed_dois)

    for depth in range(1, max_depth + 1):
        if not frontier or len(collected) >= max_papers:
            break
        expand = frontier[:MAX_FRONTIER_PER_LEVEL]
        truncated_frontier = len(frontier) - len(expand)

        tasks = [fetch(doi, direction) for doi in expand for direction in directions]
        pairs = [(doi, direction) for doi in expand for direction in directions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        new_this_level = 0
        next_frontier: list[str] = []
        for (src_doi, direction), result in zip(pairs, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("snowball_fetch_failed", doi=src_doi, error=str(result))
                continue
            for paper in result:
                if min_cited_by and paper.cited_by_count < min_cited_by:
                    continue
                if year_from and paper.year and paper.year < year_from:
                    continue
                key = _paper_key(paper)
                edge_type = "reference" if direction == "references" else "citation"
                edges.append(
                    {"from": src_doi, "to": paper.doi or paper.openalex_id,
                     "type": edge_type, "level": depth}
                )
                if key in seen:
                    continue
                seen.add(key)
                collected.append(paper)
                new_this_level += 1
                if paper.doi:
                    next_frontier.append(paper.doi)
                if len(collected) >= max_papers:
                    break
            if len(collected) >= max_papers:
                break

        level_stats.append(
            {"level": depth, "expanded_nodes": len(expand),
             "frontier_truncated": truncated_frontier, "new_papers": new_this_level}
        )
        frontier = next_frontier

    return collected, edges, level_stats


def register_snowball_tools(
    mcp: object, settings: Settings, cache: CacheStore | None = None
) -> None:
    """Register snowballing tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    from bx_scholar_core.clients.crossref import CrossRefClient, _parse_item
    from bx_scholar_core.clients.openalex import OpenAlexClient

    server: FastMCP = mcp  # type: ignore[assignment]

    async def _seed_to_doi(client: OpenAlexClient, identifier: str) -> str | None:
        resolved = resolve_id(identifier)
        if resolved.id_type == "doi":
            return resolved.value
        paper = await client.get_work(resolved.value)
        return paper.doi if paper and paper.doi else None

    @server.tool(structured_output=False)
    async def snowball(
        seed_identifiers: str,
        direction: str = "both",
        max_depth: int = 1,
        max_papers: int = 200,
        min_cited_by: int = 0,
        year_from: int | None = None,
    ) -> str:
        """Snowballing for literature reviews (Wohlin method): from seed papers, iteratively
        collect backward references and/or forward citations, deduplicated, with abstracts.

        seed_identifiers: comma-separated DOIs/arXiv/OpenAlex IDs (1-5 seeds recommended).
        direction: 'references' (backward), 'citing' (forward), or 'both'.
        max_depth: BFS iterations (1-3). max_papers: global cap (<=500).
        min_cited_by / year_from: optional inclusion filters.
        Returns JSON: {seeds, papers (sorted by cited_by_count), edges, stats}."""
        if direction not in ("references", "citing", "both"):
            return json.dumps({"error": "direction must be references|citing|both"})
        max_depth = max(1, min(max_depth, 3))
        max_papers = max(1, min(max_papers, 500))
        directions = ["references", "citing"] if direction == "both" else [direction]

        client = OpenAlexClient(settings.polite_email, settings.user_agent, cache=cache)
        try:
            raw_seeds = [s.strip() for s in seed_identifiers.split(",") if s.strip()][:5]
            seed_dois: list[str] = []
            skipped_seeds: list[str] = []
            for ident in raw_seeds:
                doi = await _seed_to_doi(client, ident)
                (seed_dois if doi else skipped_seeds).append(doi or ident)
            if not seed_dois:
                return json.dumps(
                    {"error": "no seed could be resolved to a DOI", "skipped": skipped_seeds}
                )

            async def fetch(doi: str, drc: str) -> list[Paper]:
                return await client.get_citations(doi, direction=drc, per_page=PER_NODE_RESULTS)

            papers, edges, level_stats = await snowball_bfs(
                fetch, seed_dois, directions, max_depth, max_papers,
                min_cited_by, year_from,
            )
            papers = deduplicate(papers)
            papers.sort(key=lambda p: p.cited_by_count, reverse=True)
            with_abstract = sum(1 for p in papers if p.abstract)
            return json.dumps(
                {
                    "seeds": seed_dois,
                    "skipped_seeds": skipped_seeds,
                    "direction": direction,
                    "stats": {
                        "total_papers": len(papers),
                        "with_abstract": with_abstract,
                        "edges": len(edges),
                        "levels": level_stats,
                    },
                    "papers": [p.model_dump(exclude_defaults=True) for p in papers],
                    "edges": edges,
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            logger.error("snowball_failed", error=str(exc))
            return json.dumps({"error": str(exc)})

    @server.tool(structured_output=False)
    async def resolve_reference_list(
        references_text: str,
        enrich_abstracts: bool = True,
    ) -> str:
        """Resolve a raw bibliography into verified papers with DOIs and abstracts.
        Paste the References section of a paper (one reference per line — e.g. from
        extract_pdf_text output) and each entry is matched via CrossRef bibliographic
        search, title-checked, and enriched with the OpenAlex abstract.

        Max 50 references per call. Returns JSON: {resolved: [{input, paper}],
        unresolved: [...], stats}."""
        lines = clean_reference_lines(references_text)
        dropped = max(0, len(lines) - MAX_REFERENCE_LINES)
        lines = lines[:MAX_REFERENCE_LINES]
        if not lines:
            return json.dumps({"error": "no usable reference lines found (min 25 chars each)"})

        crossref = CrossRefClient(settings.polite_email, cache=cache)
        openalex = OpenAlexClient(settings.polite_email, settings.user_agent, cache=cache)

        async def resolve_line(line: str) -> tuple[str, Paper | None]:
            try:
                resp = await crossref.get(
                    "/works",
                    params={"query.bibliographic": line, "rows": 3},
                    cache_policy=("verification", 86400),
                )
                items = resp.json().get("message", {}).get("items", [])
                for item in items:
                    title = (item.get("title") or [""])[0]
                    if title and fuzz.partial_ratio(title.lower(), line.lower()) >= TITLE_MATCH_THRESHOLD:
                        return line, _parse_item(item)
                return line, None
            except Exception as exc:
                logger.warning("resolve_reference_failed", error=str(exc))
                return line, None

        results = await asyncio.gather(*(resolve_line(line) for line in lines))

        async def enrich(paper: Paper) -> Paper:
            if paper.abstract or not paper.doi:
                return paper
            try:
                full = await openalex.get_work(paper.doi)
                if full and full.abstract:
                    paper.abstract = full.abstract
            except Exception:  # enrichment is best-effort
                pass
            return paper

        resolved_pairs = [(line, p) for line, p in results if p is not None]
        if enrich_abstracts:
            enriched = await asyncio.gather(*(enrich(p) for _, p in resolved_pairs))
            resolved_pairs = [
                (line, p) for (line, _), p in zip(resolved_pairs, enriched, strict=True)
            ]

        unresolved = [line for line, p in results if p is None]
        return json.dumps(
            {
                "resolved": [
                    {"input": line, "paper": p.model_dump(exclude_defaults=True)}
                    for line, p in resolved_pairs
                ],
                "unresolved": unresolved,
                "stats": {
                    "input_lines": len(lines),
                    "dropped_over_limit": dropped,
                    "resolved": len(resolved_pairs),
                    "unresolved": len(unresolved),
                    "with_abstract": sum(1 for _, p in resolved_pairs if p.abstract),
                },
            },
            ensure_ascii=False,
        )
