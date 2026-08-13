"""End-to-end das tools públicas, sem tocar em rede.

O v1 não tinha nenhum teste sobre ``tools/`` — a camada que é, literalmente, o
contrato com o agente. Aqui o caminho inteiro roda: fan-out → merge → gate de
integridade → persistência do pack → projeção, e depois a leitura pelo
``read_pack``.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar.store import db, packs
from bx_scholar.store.models import Integrity, Work, WorkSource
from bx_scholar.tools import pack as pack_tool
from bx_scholar.tools import search as search_tool

from conftest import make_paper, stub_connector


@pytest.fixture
def server(settings, monkeypatch):
    """Servidor com os conectores substituídos por dublês."""

    def _fake_build(_settings, _cache, names):
        catalogue = {
            "openalex": stub_connector(
                "openalex",
                [
                    make_paper("Mobilidade urbana preditiva", doi="10.1/a", cited_by=30),
                    make_paper("Transporte e cidade", doi="10.1/b", cited_by=12),
                    make_paper("Artigo problemático", doi="10.1/bad", cited_by=99),
                ],
                total=184,
            ),
            "crossref": stub_connector(
                "crossref", [make_paper("Mobilidade urbana preditiva", doi="10.1/a")]
            ),
            "brasil": stub_connector("brasil", [make_paper("Mobilidade no Brasil", doi="10.1/c")], delay=5.0),
        }
        return [catalogue[n] for n in names if n in catalogue]

    monkeypatch.setattr(search_tool, "build_connectors", _fake_build)
    monkeypatch.setattr(settings, "connector_timeout_balanced", 0.2)

    srv = FastMCP("test")
    search_tool.register(srv, settings, cache=None)
    pack_tool.register(srv, settings)
    return srv


async def call(server: FastMCP, name: str, **kwargs) -> dict:
    result = await server.call_tool(name, kwargs)
    content = result[0] if isinstance(result, tuple) else result
    text = content[0].text if isinstance(content, list) else content
    return json.loads(text)


class TestSurface:
    async def test_exactly_two_tools_registered(self, server) -> None:
        # O número é decisão de produto. No v1 eram 21 por servidor, com o
        # workflow re-registrando as mesmas 21 — 42 definições no prompt de um
        # agente que equipava os dois.
        tools = await server.list_tools()
        assert {t.name for t in tools} == {"search_literature", "read_pack"}


class TestSearchLiterature:
    async def test_happy_path(self, server, store) -> None:
        out = await call(server, "search_literature", query="mobilidade urbana")
        assert out["pack_id"].startswith("pk_")
        assert out["mode"] == "balanced"
        assert len(out["works"]) >= 2

    async def test_counts_do_not_invent_an_aggregate_total(self, server, store) -> None:
        # Somar os totais das bases superconta: as bases se sobrepõem. O que a
        # resposta mostra é o que cada base disse e o que foi consolidado.
        out = await call(server, "search_literature", query="mobilidade urbana")
        assert "works_found" not in out["counts"]
        assert out["counts"]["reported_by_source"]["openalex"] == 184
        assert out["counts"]["works_merged"] == 3
        assert out["counts"]["works_selected"] == 3

    async def test_slow_source_shows_up_as_timeout_not_as_empty(self, server, store) -> None:
        out = await call(server, "search_literature", query="mobilidade urbana")
        assert out["coverage"]["brasil"] == "timeout_partial"
        assert any("brasil" in n for n in out["limitations"])

    async def test_dedup_across_sources(self, server, store) -> None:
        out = await call(server, "search_literature", query="mobilidade urbana")
        # 10.1/a veio do OpenAlex e do CrossRef.
        assert out["counts"]["duplicates_removed"] == 1
        titles = [w["title"] for w in out["works"]]
        assert titles.count("Mobilidade urbana preditiva") == 1

    async def test_provenance_is_persisted(self, server, store) -> None:
        await call(server, "search_literature", query="mobilidade urbana")
        async with db.session() as s:
            from sqlalchemy import select

            rows = (
                await s.execute(select(WorkSource).where(WorkSource.work_key == "doi:10.1/a"))
            ).scalars().all()
        assert {r.source for r in rows} == {"openalex", "crossref"}
        assert all(r.retrieved_at > 0 for r in rows)

    async def test_empty_query_raises_instead_of_returning_fake_success(self, server, store) -> None:
        # v1: {"error": ...} com status de sucesso; o modelo tinha de adivinhar.
        with pytest.raises(Exception):
            await call(server, "search_literature", query="   ")

    async def test_invalid_mode_raises(self, server, store) -> None:
        with pytest.raises(Exception):
            await call(server, "search_literature", query="x", mode="turbo")

    async def test_deep_degrades_explicitly(self, server, store) -> None:
        out = await call(server, "search_literature", query="x", mode="deep")
        assert out["mode"] == "balanced"
        assert any("deep" in n for n in out["limitations"])


class TestIntegrityGate:
    async def _seed_retraction(self) -> None:
        async with db.session() as s:
            s.add(Integrity(doi="10.1/bad", status="retracted", nature="Retraction",
                            reasons=["Data falsificado"]))

    async def test_retracted_work_leaves_the_selection(self, server, store) -> None:
        await self._seed_retraction()
        out = await call(server, "search_literature", query="mobilidade urbana")
        titles = [w["title"] for w in out["works"]]
        assert "Artigo problemático" not in titles
        assert out["counts"]["retracted_excluded"] == 1
        assert any("retratada" in n for n in out["limitations"])

    async def test_retracted_work_stays_in_the_pack(self, server, store) -> None:
        # O bloqueio é contra citação não sinalizada, não contra a existência.
        await self._seed_retraction()
        out = await call(server, "search_literature", query="mobilidade urbana")
        item = await packs.get_item(out["pack_id"], "doi:10.1/bad")
        assert item is not None
        assert item.selected is False
        assert item.payload["integrity_status"] == "retracted"

    async def test_include_retracted_brings_it_back_still_marked(self, server, store) -> None:
        await self._seed_retraction()
        out = await call(
            server, "search_literature", query="mobilidade urbana", include_retracted=True
        )
        marked = [w for w in out["works"] if w.get("integrity") == "retracted"]
        assert len(marked) == 1

    async def test_empty_mirror_never_claims_clear(self, server, store) -> None:
        # Sem espelho populado, tudo é `unknown` — ausência de dado não é atestado.
        out = await call(server, "search_literature", query="mobilidade urbana")
        assert all(w["integrity"] == "unknown" for w in out["works"])
        assert any("NÃO verificada" in n for n in out["limitations"])

    async def test_work_without_doi_is_unknown_not_clear(self, server, store) -> None:
        await self._seed_retraction()
        async with db.session() as s:
            s.add(Integrity(doi="10.1/other", status="clear"))
        out = await call(server, "search_literature", query="mobilidade urbana")
        assert any("sem DOI" in n for n in out["limitations"]) or all(
            w["integrity"] != "unknown" for w in out["works"]
        )


class TestReadPack:
    async def test_summary_section(self, server, store) -> None:
        out = await call(server, "search_literature", query="mobilidade urbana")
        summary = await call(server, "read_pack", pack_id=out["pack_id"], section="summary")
        assert summary["status"] == "partial"  # scielo deu timeout
        assert summary["coverage"]["brasil"] == "timeout_partial"
        assert summary["workflow_version"] == "bx-scholar-v2.0"

    async def test_works_pagination(self, server, store) -> None:
        out = await call(server, "search_literature", query="mobilidade urbana")
        page = await call(server, "read_pack", pack_id=out["pack_id"], section="works", limit=1)
        assert page["returned"] == 1
        assert page["total"] >= 2
        assert page["next_offset"] == 1

    async def test_excluded_section_shows_the_retracted(self, server, store) -> None:
        async with db.session() as s:
            s.add(Integrity(doi="10.1/bad", status="retracted"))
        out = await call(server, "search_literature", query="mobilidade urbana")
        excluded = await call(server, "read_pack", pack_id=out["pack_id"], section="excluded")
        keys = [i["work_key"] for i in excluded["items"]]
        assert "doi:10.1/bad" in keys

    async def test_unknown_pack_raises_with_useful_message(self, server, store) -> None:
        with pytest.raises(Exception, match="pk_naoexiste|não existe"):
            await call(server, "read_pack", pack_id="pk_naoexiste")

    async def test_work_detail(self, server, store) -> None:
        out = await call(server, "search_literature", query="mobilidade urbana")
        detail = await call(
            server, "read_pack", pack_id=out["pack_id"], section="works", work_key="doi:10.1/a"
        )
        assert detail["work_key"] == "doi:10.1/a"
        assert detail["seen_in"] == ["crossref", "openalex"]

    async def test_job_section_is_null_when_no_deep_run(self, server, store) -> None:
        out = await call(server, "search_literature", query="x")
        job = await call(server, "read_pack", pack_id=out["pack_id"], section="job")
        assert job["job"] is None


class TestPersistence:
    async def test_repeated_search_does_not_duplicate_works(self, server, store) -> None:
        await call(server, "search_literature", query="mobilidade urbana")
        await call(server, "search_literature", query="mobilidade urbana")
        async with db.session() as s:
            from sqlalchemy import func, select

            n = (await s.execute(select(func.count()).select_from(Work))).scalar_one()
            nsrc = (await s.execute(select(func.count()).select_from(WorkSource))).scalar_one()
        # 3 obras (a, b, bad) — a do SciELO não chega, a fonte dá timeout.
        assert n == 3
        # 4 linhas de proveniência: 'a' foi vista por openalex E crossref.
        assert nsrc == 4
