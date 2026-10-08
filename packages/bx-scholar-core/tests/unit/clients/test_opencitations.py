"""Tests for the OpenCitations client and the merged citation lookups.

Fixtures tests/fixtures/opencitations_*.json are real API responses (the
citations list trimmed to 30 of its 1289 links).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar_core.citations import fetch_citations, resolve_to_doi
from bx_scholar_core.clients.opencitations import OpenCitationsClient, _ids, parse_meta
from bx_scholar_core.clients.pool import ClientPool
from bx_scholar_core.config import Settings
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.registry import register_all_tools

FIXTURES = Path(__file__).parents[2] / "fixtures"
SEED = "10.1016/j.giq.2019.06.002"


def _json(name: str):
    return json.loads((FIXTURES / f"opencitations_{name}.json").read_text())


def _mock(client, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_h))
    return seen


def _oc_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    if "/index/v2/citations/" in path:
        return httpx.Response(200, json=_json("citations"))
    if "/index/v2/references/" in path:
        return httpx.Response(200, json=_json("references"))
    if "/meta/v1/metadata/" in path:
        return httpx.Response(200, json=_json("meta"))
    return httpx.Response(404)


def _oa_work(doi: str, wid: str, title: str = "OA paper") -> dict:
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}", "title": title}


class TestParsing:
    def test_ids(self) -> None:
        ids = _ids("omid:br/061 openalex:W2955156490 doi:10.1016/j.giq.2019.06.002")
        assert ids == {
            "omid": "br/061",
            "openalex": "W2955156490",
            "doi": "10.1016/j.giq.2019.06.002",
        }

    def test_meta_record(self) -> None:
        p = parse_meta(_json("meta")[0])
        assert (
            p.title == "Artificial Intelligence In Public Administration: Partnership Or Symbiosis?"
        )
        assert p.doi == "10.26794/2304-022x-2025-15-3-90-97"
        assert p.openalex_id == "W4414548413"
        assert p.year == 2025
        assert p.authors[0].name == "M. M. Artyukhina"  # "[omid:...]" stripped, name reordered
        assert p.journal == "Management Sciences"
        assert p.issn == "2304-022X"
        assert p.source_type == "peer_reviewed"
        assert p.source_api == "opencitations"


class TestClient:
    async def test_citing_takes_the_citing_side(self) -> None:
        client = OpenCitationsClient()
        seen = _mock(client, _oc_handler)
        links = await client.links(SEED, "citing")
        assert seen[0].url.path == f"/index/v2/citations/doi:{SEED}"
        assert len(links) == 30
        assert links[0].doi == "10.26794/2304-022x-2025-15-3-90-97"
        assert all(link.doi != SEED for link in links)
        await client.close()

    async def test_references_take_the_cited_side(self) -> None:
        client = OpenCitationsClient()
        _mock(client, _oc_handler)
        links = await client.links(SEED, "references")
        assert links[0].doi == "10.1093/cje/bep051"
        assert links[0].openalex_id == "W2032654443"
        await client.close()

    async def test_unknown_doi_is_empty_but_other_errors_raise(self) -> None:
        from bx_scholar_core.clients.base import NonRetryableHTTPError

        client = OpenCitationsClient()
        _mock(client, lambda r: httpx.Response(404))
        assert await client.links(SEED, "citing") == []
        _mock(client, lambda r: httpx.Response(403))
        with pytest.raises(NonRetryableHTTPError):
            await client.links(SEED, "citing")
        await client.close()

    async def test_metadata_batches_of_25(self) -> None:
        client = OpenCitationsClient()
        seen = _mock(client, _oc_handler)
        await client.metadata([f"10.1234/{i}" for i in range(30)])
        assert len(seen) == 2
        assert seen[0].url.path.count("doi:") == 25
        assert "__" in seen[0].url.path
        await client.close()

    async def test_doi_with_query_or_fragment_marks_stays_in_the_path(self) -> None:
        """main accepts DOIs with "?" and "#" (real ones exist); unencoded they
        would cut the path and ask OpenCitations about another DOI."""
        client = OpenCitationsClient()
        seen = _mock(client, _oc_handler)
        await client.links("10.1002/(SICI)1097?x#y", "citing")
        await client.metadata(["10.1002/a?b", "10.1/c#d"])
        assert seen[0].url.raw_path.decode() == (
            "/index/v2/citations/doi:10.1002/(SICI)1097%3Fx%23y"
        )
        assert seen[0].url.query == b""
        assert b"10.1002/a%3Fb__doi:10.1/c%23d" in seen[1].url.raw_path
        await client.close()

    def test_token_header_only_when_set(self) -> None:
        assert OpenCitationsClient()._extra_headers() == {}
        assert OpenCitationsClient(token="t")._extra_headers() == {"authorization": "t"}


class TestFetchCitations:
    def _pool(self, oa_results: list[dict]) -> ClientPool:
        pool = ClientPool(Settings(polite_email="ci@bxscholar.dev", cache_enabled=False))

        def oa(req: httpx.Request) -> httpx.Response:
            if "cites:" in str(req.url.params.get("filter", "")) or req.url.path == "/works":
                return httpx.Response(200, json={"results": oa_results, "meta": {"count": 1}})
            # DOI -> OpenAlex id lookup done by OpenAlexClient.get_citations
            return httpx.Response(
                200,
                json={
                    "id": "https://openalex.org/W2955156490",
                    "cited_by_api_url": "https://api.openalex.org/works?filter=cites:W2955156490",
                    "referenced_works": [],
                },
            )

        _mock(pool.openalex, oa)
        _mock(pool.opencitations, _oc_handler)
        return pool

    async def test_merge_excludes_known_and_tags_provenance(self) -> None:
        # OpenAlex already knows the first OpenCitations link (matched by OpenAlex id)
        known = _oa_work("10.26794/2304-022X-2025-15-3-90-97", "W4414548413", "Known")
        pool = self._pool([known])

        papers, meta = await fetch_citations(pool, SEED, "citing", 5, ["openalex", "opencitations"])

        assert meta["per_source"] == {"openalex": 1, "opencitations": 5}
        assert meta["total_links"]["opencitations"] == 30
        assert meta["opencitations_not_in_openalex_page"] == 29  # 30 - 1 in the OA page
        by_doi = {p.doi.lower(): p for p in papers}
        both = by_doi["10.26794/2304-022x-2025-15-3-90-97"]
        assert both.external_ids["citation_sources"] == "openalex,opencitations"
        assert any(p.external_ids.get("citation_sources") == "opencitations" for p in papers)
        assert "errors" not in meta
        await pool.aclose()

    async def test_one_source_failing_keeps_the_other(self) -> None:
        pool = self._pool([_oa_work("10.1234/a", "W1")])
        _mock(pool.opencitations, lambda r: httpx.Response(500))
        pool.opencitations.max_retries = 1

        papers, meta = await fetch_citations(pool, SEED, "citing", 5, ["openalex", "opencitations"])

        assert [p.doi for p in papers] == ["10.1234/a"]
        assert "opencitations" in meta["errors"]
        await pool.aclose()

    async def test_invalid_doi_never_reaches_apis(self) -> None:
        pool = self._pool([])
        seen = _mock(pool.opencitations, _oc_handler)
        papers, meta = await fetch_citations(pool, "10.1/x?y=1", "citing", 5, ["opencitations"])
        assert papers == []
        assert "input" in meta["errors"]
        assert seen == []
        await pool.aclose()

    async def test_link_without_doi_not_counted_as_new(self) -> None:
        pool = self._pool([])
        rows = _json("citations")
        rows[0]["citing"] = "omid:br/1 openalex:W1"  # OpenCitations has no DOI for it
        _mock(
            pool.opencitations,
            lambda r: (
                httpx.Response(200, json=rows)
                if "/index/" in r.url.path
                else httpx.Response(200, json=_json("meta"))
            ),
        )
        _, meta = await fetch_citations(pool, SEED, "citing", 5, ["opencitations"])
        assert meta["opencitations_not_in_openalex_page"] == 29
        await pool.aclose()

    async def test_self_citation_flag(self) -> None:
        pool = self._pool([])
        rows = _json("citations")
        rows[0]["author_sc"] = "yes"
        _mock(
            pool.opencitations,
            lambda r: (
                httpx.Response(200, json=rows)
                if "/index/" in r.url.path
                else httpx.Response(200, json=_json("meta"))
            ),
        )
        papers, _ = await fetch_citations(pool, SEED, "citing", 5, ["opencitations"])
        flagged = [p.doi for p in papers if p.external_ids.get("author_self_citation")]
        assert flagged == ["10.26794/2304-022x-2025-15-3-90-97"]
        await pool.aclose()


class TestResolveToDoi:
    async def test_forms(self) -> None:
        pool = ClientPool(Settings(polite_email="ci@bxscholar.dev", cache_enabled=False))
        seen = _mock(
            pool.openalex,
            lambda r: httpx.Response(200, json=_oa_work("10.1234/z", "W9")),
        )
        assert await resolve_to_doi(pool, "https://doi.org/10.1234/a") == "10.1234/a"
        assert await resolve_to_doi(pool, "2401.12345") == "10.48550/arXiv.2401.12345"
        assert seen == []  # neither needs a lookup
        assert await resolve_to_doi(pool, "W9") == "10.1234/z"
        assert await resolve_to_doi(pool, "pmid:31398324") == "10.1234/z"
        assert [r.url.path for r in seen] == ["/works/W9", "/works/pmid:31398324"]
        assert await resolve_to_doi(pool, "not an id") is None
        await pool.aclose()


class TestTools:
    def _server(self, tmp_path):
        server = FastMCP("t")
        pool = register_all_tools(
            server,
            Settings(polite_email="ci@bxscholar.dev", cache_enabled=False),
            RankingService(data_dir=tmp_path),
        )
        return server, pool

    async def _call(self, server, tool, args) -> dict:
        out = await server.call_tool(tool, args)
        return json.loads((out[0] if isinstance(out, tuple) else out)[0].text)

    async def test_get_citations_rejects_unknown_source(self, tmp_path) -> None:
        server, pool = self._server(tmp_path)
        r = await self._call(server, "get_citations", {"identifier": SEED, "sources": "scopus"})
        assert "error" in r
        await pool.aclose()

    async def test_get_citations_direction_is_an_enum(self, tmp_path) -> None:
        server, _ = self._server(tmp_path)
        tool = next(t for t in server._tool_manager.list_tools() if t.name == "get_citations")
        assert tool.parameters["properties"]["direction"]["enum"] == ["citing", "references"]

    async def test_get_citations_opencitations_only(self, tmp_path) -> None:
        server, pool = self._server(tmp_path)
        _mock(pool.opencitations, _oc_handler)
        r = await self._call(
            server,
            "get_citations",
            {"identifier": SEED, "sources": "opencitations", "per_page": 5},
        )
        assert r["doi"] == SEED
        assert r["per_source"] == {"opencitations": 5}
        assert r["total_links"] == {"opencitations": 30}
        assert r["count"] == 5
        await pool.aclose()
