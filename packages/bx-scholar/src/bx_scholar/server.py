"""Servidor MCP do BX-Scholar v2.

Poucas tools públicas, pipelines internos ricos. O número de tools aqui é uma
decisão de produto, não um acidente: no v1 eram 21 por servidor — e o servidor
``workflow`` re-registrava as mesmas 21 do ``core``, de modo que um agente com
os dois equipados carregava 42 definições onde 21 bastavam. A medição em
produção foi de 2.734 tokens por chamada, 17,9% do prompt, com o modelo
escolhendo prefixo ao acaso entre clones idênticos.

A regra para promover uma capacidade a tool: **o agente tem razão legítima para
decidir se e quando essa operação acontece?** Se a resposta é "isso sempre
acontece nesta etapa", é workflow interno, não tool.
"""

from __future__ import annotations

import asyncio
import os
import sys

from bx_scholar_core.logging import get_logger, setup_logging
from mcp.server.fastmcp import FastMCP

from bx_scholar import __version__
from bx_scholar.cache.local import LocalHTTPCache
from bx_scholar.config import load_settings
from bx_scholar.store import db
from bx_scholar.tools import fulltext as fulltext_tool
from bx_scholar.tools import pack as pack_tool
from bx_scholar.tools import search as search_tool

logger = get_logger("bx_scholar.server")


async def _bootstrap(settings) -> None:
    """Sobe o store e limpa cache vencido antes de aceitar tráfego."""
    db.init_engine(settings.database_url)
    health = await db.healthcheck()
    logger.info("store_ready", **health)


def create_server() -> FastMCP:
    settings = load_settings()
    setup_logging(level=settings.log_level, fmt=settings.log_format)

    asyncio.run(_bootstrap(settings))

    cache = LocalHTTPCache(
        settings.cache_dir / "http", enabled=settings.cache_enabled
    )

    server = FastMCP(
        "bx-scholar",
        host=os.environ.get("BX_SCHOLAR_HOST", "127.0.0.1"),
        # Porta nova de propósito: :8097 e :8098 seguem servindo o v1 intactos
        # até a migração ser validada em preview.
        port=int(os.environ.get("BX_SCHOLAR_PORT", "8099")),
    )

    search_tool.register(server, settings, cache)
    pack_tool.register(server, settings)
    fulltext_tool.register(server, settings, cache)

    logger.info(
        "server_ready",
        version=__version__,
        tools=3,
        dialect="postgres" if settings.is_postgres else "sqlite",
        cache_enabled=settings.cache_enabled,
    )
    return server


def main() -> None:
    from bx_scholar_core.http_server import serve_http, should_serve_http

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
