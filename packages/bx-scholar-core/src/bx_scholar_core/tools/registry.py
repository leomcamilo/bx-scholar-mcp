"""Tool registry — registers all core tools on a FastMCP server."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from bx_scholar_core.config import Settings
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.cite import register_cite_tools
from bx_scholar_core.tools.fulltext import register_fulltext_tools
from bx_scholar_core.tools.get import register_get_tools
from bx_scholar_core.tools.rank import register_rank_tools
from bx_scholar_core.tools.search import register_search_tools
from bx_scholar_core.tools.verify import register_verify_tools


def register_all_tools(
    server: FastMCP,
    settings: Settings,
    ranking_service: RankingService,
) -> None:
    """Register all core MCP tools on the server."""
    register_search_tools(server, settings)
    register_get_tools(server, settings)
    register_rank_tools(server, settings, ranking_service)
    register_cite_tools(server, settings)
    register_verify_tools(server, settings)
    register_fulltext_tools(server, settings)
