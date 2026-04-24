"""Tool registry — registers all core tools on a FastMCP server."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from bx_scholar_core.config import Settings
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.cite import register_cite_tools
from bx_scholar_core.tools.fulltext import register_fulltext_tools
from bx_scholar_core.tools.get import register_get_tools
from bx_scholar_core.tools.rank import register_rank_tools
from bx_scholar_core.tools.search import register_search_tools
from bx_scholar_core.tools.verify import register_verify_tools

if TYPE_CHECKING:
    from bx_scholar_core.cache import CacheStore


def register_all_tools(
    server: FastMCP,
    settings: Settings,
    ranking_service: RankingService,
    cache: CacheStore | None = None,
) -> None:
    """Register all core MCP tools on the server."""
    register_search_tools(server, settings, cache)
    register_get_tools(server, settings, cache)
    register_rank_tools(server, settings, ranking_service)
    register_cite_tools(server, settings, cache)
    register_verify_tools(server, settings, cache)
    register_fulltext_tools(server, settings, cache)
