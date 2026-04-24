"""Get tools — paper by ID, author profile, journal info."""

from __future__ import annotations

import json

from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.config import Settings
from bx_scholar_core.id_resolver import resolve_id
from bx_scholar_core.logging import get_logger

logger = get_logger(__name__)


def register_get_tools(mcp: object, settings: Settings) -> None:
    """Register get/metadata tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]

    @server.tool()
    async def get_paper(identifier: str) -> str:
        """Get full metadata for a paper by DOI, arXiv ID, or OpenAlex ID.
        Accepts: '10.1234/test', 'https://doi.org/10.1234/test', 'W12345', '2401.12345'."""
        resolved = resolve_id(identifier)
        client = OpenAlexClient(settings.polite_email, settings.user_agent)
        try:
            if resolved.id_type == "doi":
                paper = await client.get_work(resolved.value)
            elif resolved.id_type == "openalex":
                resp = await client.get(
                    f"/works/https://openalex.org/{resolved.value}",
                    params=client._default_params(),
                )
                from bx_scholar_core.clients.openalex import _parse_work

                paper = _parse_work(resp.json())
            elif resolved.id_type == "arxiv":
                paper = await client.get_work(resolved.value)
                if not paper:
                    # Try OpenAlex with arXiv filter
                    papers, _ = await client.search(resolved.value, per_page=1)
                    paper = papers[0] if papers else None
            else:
                paper = await client.get_work(resolved.value)

            if paper:
                return json.dumps(
                    {"paper": paper.model_dump(exclude_defaults=True)},
                    ensure_ascii=False,
                    indent=2,
                )
            return json.dumps({"error": f"Paper not found: {identifier}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        finally:
            await client.close()

    @server.tool()
    async def get_author(
        author_name: str,
        per_page: int = 20,
    ) -> str:
        """Get author profile and works sorted by citation count.
        Returns h-index, works count, cited-by count, and top papers."""
        client = OpenAlexClient(settings.polite_email, settings.user_agent)
        try:
            result = await client.get_author(author_name, per_page)
            if result:
                author_data = result["author"]
                works_data = result["works"]
                return json.dumps(
                    {
                        "author": author_data.model_dump(exclude_defaults=True),
                        "works": [w.model_dump(exclude_defaults=True) for w in works_data],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            return json.dumps({"error": f"Author not found: {author_name}"})
        finally:
            await client.close()

    @server.tool()
    async def get_journal_info(issn_or_name: str) -> str:
        """Get journal metadata including impact metrics, scope, and rankings (SJR/Qualis/JQL).
        Accepts ISSN (e.g. '0001-4273') or journal name."""
        client = OpenAlexClient(settings.polite_email, settings.user_agent)
        try:
            source = await client.get_source(issn_or_name)
            if not source:
                return json.dumps({"error": f"Journal not found: {issn_or_name}"})

            result = {
                "name": source.get("display_name", ""),
                "issn_l": source.get("issn_l", ""),
                "issns": source.get("issn", []),
                "works_count": source.get("works_count"),
                "cited_by_count": source.get("cited_by_count"),
                "h_index": (source.get("summary_stats") or {}).get("h_index"),
                "type": source.get("type", ""),
                "publisher": (
                    (source.get("host_organization_lineage_names") or [""])[0]
                    if source.get("host_organization_lineage_names")
                    else ""
                ),
                "subjects": [
                    c.get("display_name", "") for c in (source.get("x_concepts") or [])[:5]
                ],
                "is_open_access": source.get("is_oa", False),
            }
            return json.dumps(result, ensure_ascii=False, indent=2)
        finally:
            await client.close()

    @server.tool()
    async def get_citations(
        identifier: str,
        direction: str = "citing",
        per_page: int = 25,
    ) -> str:
        """Get papers that cite this paper (citing) or papers cited by it (references).
        Essential for snowballing. Accepts DOI or OpenAlex ID."""
        resolved = resolve_id(identifier)
        doi = resolved.value if resolved.id_type == "doi" else identifier
        client = OpenAlexClient(settings.polite_email, settings.user_agent)
        try:
            papers = await client.get_citations(doi, direction, per_page)
            return json.dumps(
                {
                    "direction": direction,
                    "identifier": identifier,
                    "count": len(papers),
                    "results": [p.model_dump(exclude_defaults=True) for p in papers],
                },
                ensure_ascii=False,
                indent=2,
            )
        finally:
            await client.close()

    @server.tool()
    async def get_keyword_trends(
        keywords: str,
        year_from: int = 2015,
        year_to: int = 2025,
    ) -> str:
        """Track keyword frequency in academic publications over time.
        keywords: comma-separated (max 5). Returns yearly counts per keyword."""
        kw_list = [k.strip() for k in keywords.split(",")][:5]
        client = OpenAlexClient(settings.polite_email, settings.user_agent)
        try:
            trends: dict[str, dict[int, int]] = {}
            for kw in kw_list:
                trends[kw] = await client.get_keyword_counts(kw, year_from, year_to)
            return json.dumps({"keyword_trends": trends}, ensure_ascii=False, indent=2)
        finally:
            await client.close()
