"""BX-Scholar Core MCP Server entry point."""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from bx_scholar_core.cache import CacheStore
from bx_scholar_core.config import load_settings
from bx_scholar_core.http_server import serve_http, should_serve_http
from bx_scholar_core.logging import get_logger, setup_logging
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.registry import register_all_tools


def create_server() -> FastMCP:
    """Create and configure the BX-Scholar Core MCP server."""
    settings = load_settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)
    logger = get_logger("bx_scholar_core.server")

    # Load ranking data
    ranking_service = RankingService(data_dir=settings.data_dir)
    ranking_service.load()

    # Initialize cache
    cache: CacheStore | None = None
    if settings.cache_enabled and settings.cache_dir:
        cache = CacheStore(db_path=settings.cache_dir / "bx_scholar_cache.duckdb")
        logger.info("cache_initialized", path=str(settings.cache_dir / "bx_scholar_cache.duckdb"))

    # Create MCP server and register tools. host/port matter only for the
    # streamable-http transport; passing them at construction lets FastMCP
    # auto-enable DNS-rebinding protection for the loopback bind.
    server = FastMCP(
        "bx-scholar-core",
        host=os.environ.get("BX_SCHOLAR_HOST", "127.0.0.1"),
        port=int(os.environ.get("BX_SCHOLAR_PORT", "8097")),
    )
    register_all_tools(server, settings, ranking_service, cache)

    logger.info(
        "server_ready",
        tools=19,
        data_dir=str(settings.data_dir),
        cache_enabled=settings.cache_enabled,
    )

    return server


def main() -> None:
    """CLI entry point for bx-scholar-core."""
    try:
        server = create_server()
        if should_serve_http():
            serve_http(server)
        else:
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
