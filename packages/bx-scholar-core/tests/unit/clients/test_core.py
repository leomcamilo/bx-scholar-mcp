"""CORE client and its use in get_fulltext, check_open_access and search_papers.

Fixtures are real CORE v3 responses recorded on 2026-10-08 without an API key
(core_search: "urban mobility prediction"; core_by_doi: 10.1371/journal.pone.0185809).
With a key CORE returns the extracted text in ``fullText``; that response is
derived here from the recorded one, since no key was available.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar_core.clients.core import HIDDEN_FULLTEXT, CoreClient, fulltext_of, parse_work
from bx_scholar_core.config import Settings
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.registry import register_all_tools

FIXTURES = Path(__file__).parents[2] / "fixtures"
DOI = "10.1371/journal.pone.0185809"


def _json(name: str) -> dict:
    return json.loads((FIXTURES / f"core_{name}.json").read_text())


def _with_fulltext(text: str) -> dict:
    data = copy.deepcopy(_json("by_doi"))
    data["results"][0]["fullText"] = text
    return data


def _client(handler, api_key: str = "") -> tuple[CoreClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    client = CoreClient(api_key)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_h))
    return client, seen


def _q(req: httpx.Request) -> str:
    return parse_qs(urlparse(str(req.url)).query)["q"][0]


class TestParsing:
    def test_search_record(self) -> None:
        p = parse_work(_json("search")["results"][0])
        assert p.title == "Limits of predictability for large-scale urban vehicular mobility"
        assert p.doi == "10.1109/tits.2014.2325395"
        assert p.year == 2014
        assert p.language == "en"
        assert p.source_api == "core"
        assert p.external_ids == {"core": "17398046"}
        assert p.landing_url == "https://core.ac.uk/works/17398046"
        assert not p.is_open_access and not p.pdf_url  # no downloadUrl in this record

    def test_last_first_names_keep_their_split(self) -> None:
        a = parse_work(_json("search")["results"][0]).authors[0]
        assert (a.family, a.given, a.name, a.structure_source) == (
            "Chen", "Sheng", "Sheng Chen", "source",
        )  # fmt: skip

    def test_first_last_names_are_not_guessed(self) -> None:
        a = parse_work(_json("by_doi")["results"][0]).authors[0]
        assert (a.name, a.family, a.structure_source) == ("Dave Goulson", "", "none")

    def test_download_url_is_the_pdf(self) -> None:
        p = parse_work(_json("by_doi")["results"][0])
        assert p.pdf_url == "https://core.ac.uk/download/132289095.pdf"
        assert p.is_open_access

    def test_hidden_fulltext_is_no_fulltext(self) -> None:
        work = _json("by_doi")["results"][0]
        assert work["fullText"] == HIDDEN_FULLTEXT
        assert fulltext_of(work) == ""
        assert fulltext_of(_with_fulltext("Abstract. Insects.")["results"][0]) == (
            "Abstract. Insects."
        )


class TestClient:
    async def test_search_query_and_path(self) -> None:
        client, seen = _client(lambda r: httpx.Response(200, json=_json("search")))
        papers, total = await client.search("urban mobility", 2020, 2021, limit=500)
        assert seen[0].url.path == "/v3/search/works/"  # without the slash CORE answers 301
        assert _q(seen[0]) == "(urban mobility) AND yearPublished>=2020 AND yearPublished<=2021"
        assert parse_qs(urlparse(str(seen[0].url)).query)["limit"] == ["100"]
        assert total == 4423160
        assert len(papers) == 3
        await client.close()

    async def test_by_doi_quotes_the_doi_and_checks_the_hit(self) -> None:
        client, seen = _client(lambda r: httpx.Response(200, json=_json("by_doi")))
        work = await client.by_doi(DOI.upper())
        assert work is not None and work["id"] == 8020617
        assert _q(seen[0]) == f'doi:"{DOI.upper()}"'
        assert await client.by_doi("10.1371/journal.pone.0000001") is None  # other DOI's hit
        await client.close()

    async def test_by_doi_rejects_injection_before_any_request(self) -> None:
        client, seen = _client(lambda r: httpx.Response(200, json=_json("by_doi")))
        with pytest.raises(ValueError):
            await client.by_doi('10.1/x" OR title:"y')
        assert seen == []
        await client.close()

    def test_key_header_only_when_set(self) -> None:
        assert CoreClient()._extra_headers() == {}
        assert CoreClient("k")._extra_headers() == {"Authorization": "Bearer k"}


class TestTools:
    def _server(self, tmp_path, core_handler, unpaywall_handler=None, core_key: str = ""):
        server = FastMCP("t")
        pool = register_all_tools(
            server,
            Settings(polite_email="ci@bxscholar.dev", cache_enabled=False, core_api_key=core_key),
            RankingService(data_dir=tmp_path),
        )
        empty_epmc = {"hitCount": 0, "resultList": {"result": []}}
        pool.europepmc._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=empty_epmc))
        )
        self.core_seen: list[httpx.Request] = []

        def _core(req: httpx.Request) -> httpx.Response:
            self.core_seen.append(req)
            return core_handler(req)

        pool.core._client = httpx.AsyncClient(transport=httpx.MockTransport(_core))
        if unpaywall_handler:
            pool.unpaywall._client = httpx.AsyncClient(
                transport=httpx.MockTransport(unpaywall_handler)
            )
        return server, pool

    async def _call(self, server, tool: str, args: dict) -> dict:
        out = await server.call_tool(tool, args)
        blocks = out[0] if isinstance(out, tuple) else out
        return json.loads(blocks[0].text)

    async def test_fulltext_from_core_with_a_key(self, tmp_path) -> None:
        text = "Introduction. Flying insect biomass declined. " * 20
        server, pool = self._server(
            tmp_path, lambda r: httpx.Response(200, json=_with_fulltext(text)), core_key="k"
        )
        r = await self._call(server, "get_fulltext", {"identifier": DOI, "max_chars": 100})
        assert r["available"] is True
        assert r["source"] == "core"
        assert r["core_id"] == "8020617"
        assert r["headings"] == ["Full text"]
        assert len(r["sections"][0]["text"]) == 100
        assert r["truncated_section"] == "Full text"
        assert self.core_seen[0].headers["authorization"] == "Bearer k"
        await pool.aclose()

    async def test_without_a_key_points_to_core_pdf(self, tmp_path) -> None:
        server, pool = self._server(tmp_path, lambda r: httpx.Response(200, json=_json("by_doi")))
        r = await self._call(server, "get_fulltext", {"identifier": DOI})
        assert r["available"] is False
        assert r["pdf_url"] == "https://core.ac.uk/download/132289095.pdf"
        assert "CORE_API_KEY" in r["reason"]
        assert "download_pdf" in r["next_step"]
        await pool.aclose()

    async def test_core_failure_is_an_error_not_unavailable(self, tmp_path) -> None:
        server, pool = self._server(tmp_path, lambda r: httpx.Response(403))
        r = await self._call(server, "get_fulltext", {"identifier": DOI})
        assert "available" not in r
        assert "CORE request failed" in r["error"]
        await pool.aclose()

    async def test_open_access_falls_back_to_core(self, tmp_path) -> None:
        closed = {"title": "T", "oa_status": "closed", "is_oa": False, "best_oa_location": None}
        server, pool = self._server(
            tmp_path,
            lambda r: httpx.Response(200, json=_json("by_doi")),
            lambda r: httpx.Response(200, json=closed),
        )
        r = await self._call(server, "check_open_access", {"doi": DOI})
        assert r["pdf_url"] == "https://core.ac.uk/download/132289095.pdf"
        assert r["pdf_source"] == "core"
        assert r["core_landing_url"] == "https://core.ac.uk/works/8020617"
        await pool.aclose()

    async def test_open_access_keeps_unpaywall_when_it_has_a_pdf(self, tmp_path) -> None:
        oa = {
            "title": "T",
            "oa_status": "gold",
            "is_oa": True,
            "best_oa_location": {"url_for_pdf": "https://journals.plos.org/x.pdf"},
        }
        server, pool = self._server(
            tmp_path,
            lambda r: httpx.Response(200, json=_json("by_doi")),
            lambda r: httpx.Response(200, json=oa),
        )
        r = await self._call(server, "check_open_access", {"doi": DOI})
        assert r["pdf_source"] == "unpaywall"
        assert self.core_seen == []
        await pool.aclose()

    async def test_oa_preset_searches_core(self, tmp_path) -> None:
        server, pool = self._server(tmp_path, lambda r: httpx.Response(200, json=_json("search")))
        pool.openalex._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"results": [], "meta": {"count": 0}})
            )
        )
        r = await self._call(server, "search_papers", {"query": "urban mobility", "sources": "oa"})
        assert {p["source_api"] for p in r["results"]} == {"core"}
        await pool.aclose()
