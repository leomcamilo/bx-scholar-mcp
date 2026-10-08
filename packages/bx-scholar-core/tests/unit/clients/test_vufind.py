"""Tests for the VuFind clients (BDTD, OasisBR, LA Referencia) and search_theses.

Fixtures tests/fixtures/vufind_*.json are real API responses.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar_core.clients.base import NonRetryableHTTPError
from bx_scholar_core.clients.vufind import (
    BDTDClient,
    LAReferenciaClient,
    OasisBRClient,
    _person,
)
from bx_scholar_core.config import Settings
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.registry import register_all_tools

FIXTURES = Path(__file__).parents[2] / "fixtures"


def _json(name: str) -> dict:
    return json.loads((FIXTURES / f"vufind_{name}.json").read_text())


def _mock(client, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_h))
    return seen


def _params(req: httpx.Request) -> list[tuple[str, str]]:
    return list(req.url.params.multi_items())


class TestParse:
    def test_bdtd_thesis(self) -> None:
        rec = _json("bdtd")["records"][2]
        p = BDTDClient().parse_record(rec)
        assert p.title.startswith("Avaliação ex ante da política setorial")
        assert p.source_type == "thesis"
        assert p.year == 2019
        assert p.authors[0].name == "Laura Machado"
        assert p.language == "pt"
        assert p.external_ids["format"] == "doctoralThesis"
        assert p.external_ids["institution"] == "UFRGS"
        assert p.external_ids["bdtd"] == rec["id"]
        assert p.landing_url == "http://hdl.handle.net/10183/194637"
        assert p.source_api == "bdtd"
        assert p.abstract

    def test_oasisbr_article_without_summary(self) -> None:
        rec = _json("oasisbr")["records"][2]  # STJ record: empty summary
        p = OasisBRClient().parse_record(rec)
        assert p.source_type == "peer_reviewed"
        assert p.abstract == ""
        # secondary authors are only advisors on theses, not on articles
        assert "advisors" not in p.external_ids

    def test_lareferencia_country_and_name_without_comma(self) -> None:
        rec = _json("lareferencia")["records"][1]
        p = LAReferenciaClient().parse_record(rec)
        assert p.external_ids["country"] == "México"
        assert p.authors[0].name == "Fernando Calonge Reillo"
        assert p.language == ""  # record has no language

    def test_advisors_from_secondary_authors_on_theses(self) -> None:
        rec = {
            "id": "X",
            "title": "T",
            "formats": ["doctoralThesis"],
            "authors": {"primary": {"Silva, Ana": []}, "secondary": {"Souza, Beto": []}},
        }
        p = BDTDClient().parse_record(rec)
        assert p.external_ids["advisors"] == "Beto Souza"

    def test_landing_url_falls_back_to_record_page(self) -> None:
        p = BDTDClient().parse_record({"id": "ABC_1", "title": "T"})
        assert p.landing_url == "https://bdtd.ibict.br/vufind/Record/ABC_1"
        assert not p.is_open_access

    def test_person(self) -> None:
        assert _person("Neves, Talita D' Almeida") == "Talita D' Almeida Neves"
        assert _person("Fernando Calonge Reillo") == "Fernando Calonge Reillo"
        assert _person("IBICT,") == "IBICT,"


class TestSearch:
    async def test_query_params(self) -> None:
        client = LAReferenciaClient()
        seen = _mock(client, lambda r: httpx.Response(200, json=_json("lareferencia")))

        papers, total = await client.search(
            "movilidad urbana",
            year_from=2020,
            year_to=2022,
            limit=3,
            formats=("masterThesis", "doctoralThesis"),
        )

        params = _params(seen[0])
        assert seen[0].url.path == "/vufind/api/v1/search"
        assert ("type", "AllField") in params  # LA Referencia's singular handler name
        assert ("filter[]", '~format:"masterThesis"') in params
        assert ("filter[]", '~format:"doctoralThesis"') in params
        assert ("publishDatefrom", "2020") in params
        assert ("publishDateto", "2022") in params
        assert ("field[]", "title") in params
        assert total == 3513
        assert len(papers) == 3
        await client.close()

    async def test_bdtd_uses_plural_handler_and_no_date_range_by_default(self) -> None:
        client = BDTDClient()
        seen = _mock(client, lambda r: httpx.Response(200, json=_json("bdtd")))
        await client.search("mobilidade urbana")
        params = _params(seen[0])
        assert ("type", "AllFields") in params
        assert not any(k == "daterange[]" for k, _ in params)
        await client.close()

    async def test_error_status_raises(self) -> None:
        client = LAReferenciaClient()
        _mock(
            client,
            lambda r: httpx.Response(400, json={"status": "ERROR", "statusMessage": "Invalid"}),
        )
        with pytest.raises(NonRetryableHTTPError):
            await client.search("x")
        await client.close()

    async def test_ok_http_but_error_status_raises(self) -> None:
        client = BDTDClient()
        _mock(
            client,
            lambda r: httpx.Response(200, json={"status": "ERROR", "statusMessage": "boom"}),
        )
        with pytest.raises(RuntimeError, match="boom"):
            await client.search("x")
        await client.close()


class TestSearchThesesTool:
    def _server(self, tmp_path):
        server = FastMCP("t")
        pool = register_all_tools(
            server,
            Settings(polite_email="ci@bxscholar.dev", cache_enabled=False),
            RankingService(data_dir=tmp_path),
        )
        return server, pool

    async def _call(self, server, args) -> dict:
        out = await server.call_tool("search_theses", args)
        blocks = out[0] if isinstance(out, tuple) else out
        return json.loads(blocks[0].text)

    async def test_br_doctoral(self, tmp_path) -> None:
        server, pool = self._server(tmp_path)
        seen = _mock(pool.bdtd, lambda r: httpx.Response(200, json=_json("bdtd")))

        r = await self._call(server, {"query": "mobilidade urbana", "degree": "doctoral"})

        assert r["per_source"] == {"bdtd": 3}
        assert ("filter[]", '~format:"doctoralThesis"') in _params(seen[0])
        assert not any("masterThesis" in v for _, v in _params(seen[0]))
        await pool.aclose()

    async def test_latam_queries_both_and_dedups(self, tmp_path) -> None:
        server, pool = self._server(tmp_path)
        bdtd = _json("bdtd")
        # Same thesis harvested by both portals (LA Referencia aggregates Brazil too)
        dup = dict(bdtd["records"][0], id="BR_dup")
        lar = {"status": "OK", "resultCount": 1, "records": [dup]}
        _mock(pool.bdtd, lambda r: httpx.Response(200, json=bdtd))
        _mock(pool.lareferencia, lambda r: httpx.Response(200, json=lar))

        r = await self._call(server, {"query": "mobilidade urbana", "scope": "latam"})

        assert r["per_source"] == {"bdtd": 3, "lareferencia": 1}
        assert r["duplicates_removed"] == 1
        assert r["returned"] == 3
        await pool.aclose()

    async def test_invalid_scope(self, tmp_path) -> None:
        server, pool = self._server(tmp_path)
        r = await self._call(server, {"query": "x", "scope": "europe"})
        assert "error" in r
        await pool.aclose()

    async def test_search_papers_br_preset_includes_repositories(self, tmp_path) -> None:
        server, pool = self._server(tmp_path)
        _mock(pool.bdtd, lambda r: httpx.Response(200, json=_json("bdtd")))
        _mock(pool.oasisbr, lambda r: httpx.Response(200, json=_json("oasisbr")))
        _mock(pool.scielo, lambda r: httpx.Response(200, json={"results": [], "meta": {}}))
        _mock(pool.openalex, lambda r: httpx.Response(200, json={"results": [], "meta": {}}))

        out = await server.call_tool("search_papers", {"query": "x", "sources": "br"})
        r = json.loads((out[0] if isinstance(out, tuple) else out)[0].text)

        assert r["sources_queried"] == ["scielo", "pt", "bdtd", "oasisbr"]
        assert r["per_source"]["bdtd"] == 3
        await pool.aclose()
