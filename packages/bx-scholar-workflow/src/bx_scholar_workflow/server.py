"""BX-Scholar Workflow MCP Server — core tools + research prompts + skill resources."""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from bx_scholar_core.cache import CacheStore
from bx_scholar_core.config import load_settings
from bx_scholar_core.logging import get_logger, setup_logging
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.registry import register_all_tools
from bx_scholar_workflow.prompts.loader import register_all_prompts
from bx_scholar_workflow.skills.loader import register_all_skills


def create_server() -> FastMCP:
    """Create the BX-Scholar Workflow MCP server.

    Composes core tools (19) + workflow prompts (8) + skill resources (21)
    into a single MCP server.
    """
    settings = load_settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)
    logger = get_logger("bx_scholar_workflow.server")

    # Load ranking data
    ranking_service = RankingService(data_dir=settings.data_dir)
    ranking_service.load()

    # Initialize cache
    cache: CacheStore | None = None
    if settings.cache_enabled and settings.cache_dir:
        cache = CacheStore(db_path=settings.cache_dir / "bx_scholar_cache.duckdb")

    # Create server and register everything
    server = FastMCP("bx-scholar-workflow")
    register_all_tools(server, settings, ranking_service, cache)
    register_all_prompts(server)
    register_all_skills(server)

    logger.info("server_ready", tools=19, prompts=8, skills=21)
    return server


def main() -> None:
    """CLI entry point for bx-scholar-workflow."""
    try:
        server = create_server()
        server.run()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
