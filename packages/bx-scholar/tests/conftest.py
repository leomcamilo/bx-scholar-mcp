"""Fixtures do BX-Scholar v2.

Nada aqui bate em API real: os conectores são substituídos por dublês que
devolvem ``Paper`` fabricado, ou que estouram tempo/erro sob comando. É assim
que se testa o comportamento de cobertura — o v1 não tinha teste nenhum sobre a
camada de tools, justamente onde mora o contrato com o agente.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from bx_scholar_core.models.paper import Author, Paper

from bx_scholar.config import Settings
from bx_scholar.connectors.registry import Connector, SearchRequest
from bx_scholar.store import db


def make_paper(
    title: str = "Um estudo",
    *,
    doi: str = "",
    year: int | None = 2024,
    journal: str = "Revista X",
    abstract: str = "",
    cited_by: int = 0,
    source_api: str = "openalex",
    openalex_id: str = "",
) -> Paper:
    return Paper(
        title=title,
        doi=doi,
        year=year,
        journal=journal,
        abstract=abstract,
        cited_by_count=cited_by,
        source_api=source_api,
        openalex_id=openalex_id,
        authors=[Author(name="Autor A")],
    )


def stub_connector(
    name: str,
    papers: list[Paper] | None = None,
    *,
    total: int | None = None,
    raises: Exception | None = None,
    delay: float = 0.0,
    essential: bool = False,
) -> Connector:
    """Conector de mentira, com falha e lentidão sob controle."""

    async def _search(req: SearchRequest) -> tuple[list[Paper], int]:
        if delay:
            await asyncio.sleep(delay)
        if raises is not None:
            raise raises
        found = papers or []
        return found, total if total is not None else len(found)

    async def _close() -> None:
        return None

    return Connector(name=name, search=_search, close=_close, essential=essential)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        polite_email="leo@baxi.ia.br",
        cache_enabled=False,
        projection_max_chars=12000,
        projection_max_works=25,
    )


@pytest_asyncio.fixture
async def store():
    """Store limpo por teste.

    Por padrão usa SQLite em arquivo temporário. Definindo
    ``BX_SCHOLAR_TEST_DATABASE_URL`` a MESMA suíte roda contra Postgres — é
    assim que a promessa de dialeto-agnóstico é verificada em vez de assumida.
    O Postgres tem comportamentos que o SQLite não tem (VARCHAR(n) é imposto,
    tipos são estritos), então rodar só num dos dois esconde defeito.
    """
    import os

    url = os.environ.get("BX_SCHOLAR_TEST_DATABASE_URL")
    if url:
        db.init_engine(url)
        await db.drop_all()
        await db.create_all()
        yield
        await db.drop_all()
        await db.dispose()
        return

    tmp = Path(tempfile.mkdtemp()) / "test.db"
    db.init_engine(f"sqlite+aiosqlite:///{tmp}")
    await db.create_all()
    yield
    await db.dispose()
