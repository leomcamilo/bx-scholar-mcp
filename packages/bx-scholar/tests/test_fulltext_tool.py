"""``retrieve_fulltext``: base de verificação sempre explícita.

O teste central deste arquivo é o de degradação: quando só há resumo, a
resposta precisa DIZER que só há resumo. É o campo que impede o modelo de
transformar "o título menciona" em "o estudo comprova".
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar.store import db
from bx_scholar.store.models import FulltextDoc, Work
from bx_scholar.tools import fulltext as fulltext_tool


@pytest.fixture
def server(settings):
    srv = FastMCP("test")
    fulltext_tool.register(srv, settings, cache=None)
    return srv


async def call(server: FastMCP, name: str, **kwargs) -> dict:
    result = await server.call_tool(name, kwargs)
    content = result[0] if isinstance(result, tuple) else result
    text = content[0].text if isinstance(content, list) else content
    return json.loads(text)


async def seed_work(**overrides) -> str:
    payload = {
        "title": "Arborizacao urbana e temperatura",
        "abstract": "Avaliamos o efeito da arborizacao na temperatura de cidades brasileiras. "
        "Foram usadas estacoes meteorologicas ao longo de dezoito meses.",
        "doi": "10.1590/abc",
        **overrides.pop("payload", {}),
    }
    work_key = overrides.pop("work_key", "doi:10.1590/abc")
    fields = {
        "work_key": work_key,
        "doi": payload.get("doi"),
        "title": payload["title"],
        "year": 2023,
        "payload": payload,
        "integrity_status": "clear",
        **overrides,
    }
    async with db.session() as s:
        s.add(Work(**fields))
    return work_key


class TestVerificationBasis:
    async def test_abstract_only_says_so_loudly(self, server, store) -> None:
        await seed_work()
        out = await call(server, "retrieve_fulltext", work="10.1590/abc",
                         question="quantas estacoes meteorologicas")
        assert out["access_status"] == "abstract_only"
        assert out["verification_basis"] == "abstract"
        assert any("resumo" in n.lower() for n in out["limitations"])

    async def test_no_text_at_all_is_metadata_only(self, server, store) -> None:
        await seed_work(work_key="doi:10.1/vazio",
                        payload={"abstract": "", "doi": "10.1/vazio"})
        out = await call(server, "retrieve_fulltext", work="doi:10.1/vazio", question="qualquer")
        assert out["access_status"] == "closed"
        assert out["verification_basis"] == "metadata_only"
        assert any("metadados" in n.lower() for n in out["limitations"])

    async def test_spans_come_from_the_abstract_when_that_is_all_there_is(
        self, server, store
    ) -> None:
        await seed_work()
        out = await call(server, "retrieve_fulltext", work="10.1590/abc",
                         question="estacoes meteorologicas")
        assert out["spans"]
        assert out["spans"][0]["section"] == "abstract"
        assert out["verification_basis"] == "abstract"


class TestBehaviour:
    async def test_no_match_is_not_a_contradiction(self, server, store) -> None:
        await seed_work()
        out = await call(server, "retrieve_fulltext", work="10.1590/abc",
                         question="blockchain criptomoeda ethereum")
        assert out["spans"] == []
        assert any("não localizamos evidência" in n for n in out["limitations"])
        assert any("não que o documento contradiga" in n for n in out["limitations"])

    async def test_without_question_returns_no_spans_and_explains(self, server, store) -> None:
        await seed_work()
        out = await call(server, "retrieve_fulltext", work="10.1590/abc")
        assert out["spans"] == []
        assert any("Sem `question`" in n for n in out["limitations"])

    async def test_unknown_work_raises_with_actionable_message(self, server, store) -> None:
        with pytest.raises(Exception, match="search_literature|não está no store"):
            await call(server, "retrieve_fulltext", work="10.9999/inexistente")

    async def test_empty_reference_raises(self, server, store) -> None:
        with pytest.raises(Exception):
            await call(server, "retrieve_fulltext", work="  ")

    async def test_accepts_doi_url_and_work_key_alike(self, server, store) -> None:
        await seed_work()
        for form in ("10.1590/abc", "https://doi.org/10.1590/abc", "doi:10.1590/abc"):
            out = await call(server, "retrieve_fulltext", work=form)
            assert out["work_key"] == "doi:10.1590/abc"

    async def test_document_is_persisted_and_reused(self, server, store) -> None:
        await seed_work()
        first = await call(server, "retrieve_fulltext", work="10.1590/abc", question="temperatura")
        second = await call(server, "retrieve_fulltext", work="10.1590/abc", question="temperatura")
        assert first["document_id"] == second["document_id"]

        async with db.session() as s:
            from sqlalchemy import func, select

            n = (await s.execute(select(func.count()).select_from(FulltextDoc))).scalar_one()
        assert n == 1

    async def test_integrity_travels_with_the_document(self, server, store) -> None:
        # Quem pede o texto de um artigo retratado precisa ver isso aqui também,
        # não só na busca — a tool pode ser chamada direto com um DOI.
        await seed_work(work_key="doi:10.1/ret", payload={"doi": "10.1/ret", "abstract": "x y z"},
                        integrity_status="retracted")
        out = await call(server, "retrieve_fulltext", work="doi:10.1/ret")
        assert out["integrity_status"] == "retracted"
