"""Ranking tools — journal lookup, top journals for field."""

from __future__ import annotations

import json

from bx_scholar_core.config import Settings
from bx_scholar_core.logging import get_logger
from bx_scholar_core.rankings.service import RankingService

logger = get_logger(__name__)


def register_rank_tools(mcp: object, settings: Settings, ranking_service: RankingService) -> None:
    """Register ranking-related tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]

    @server.tool()
    async def rank_journal(issn_or_name: str) -> str:
        """Look up journal ranking in local SJR + Qualis + JQL databases.
        Fast local lookup (no API call). Accepts ISSN or journal name.
        Supports fuzzy name matching (>85% similarity)."""
        metrics = ranking_service.lookup(issn_or_name)

        if not metrics.sjr and not metrics.qualis and not metrics.jql:
            return json.dumps(
                {
                    "error": f"Journal not found in local rankings: {issn_or_name}",
                    "suggestion": "Try get_journal_info for OpenAlex metadata lookup",
                }
            )

        result: dict[str, object] = {"query": issn_or_name, "issn": metrics.issn}

        if metrics.sjr:
            result["sjr"] = metrics.sjr.model_dump()
        if metrics.qualis:
            result["qualis"] = metrics.qualis.model_dump()
        if metrics.jql:
            result["jql"] = metrics.jql.model_dump()

        result["best_tier"] = metrics.best_tier
        return json.dumps(result, ensure_ascii=False, indent=2)

    @server.tool()
    async def top_journals_for_field(
        field: str,
        limit: int = 20,
    ) -> str:
        """Get top-ranked journals for a research field based on SJR score.
        Returns journals sorted by SJR descending with Qualis and JQL when available."""
        top = ranking_service.top_journals_for_field(field, limit)
        results = []
        for m in top:
            entry: dict[str, object] = {
                "title": m.title,
                "issn": m.issn,
                "best_tier": m.best_tier,
            }
            if m.sjr:
                entry["sjr_score"] = m.sjr.sjr_score
                entry["quartile"] = m.sjr.quartile
                entry["h_index"] = m.sjr.h_index
            if m.qualis:
                entry["qualis"] = m.qualis.classification
            if m.jql:
                entry["jql_abs"] = m.jql.abs
            results.append(entry)

        return json.dumps(
            {"field": field, "count": len(results), "top_journals": results},
            ensure_ascii=False,
            indent=2,
        )
