"""Tests for the client pool, the search source registry and search_papers."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar_core.clients.openalex import OpenAlexClient, _parse_work
from bx_scholar_core.clients.pool import ClientPool
from bx_scholar_core.config import Settings
from bx_scholar_core.dedup import deduplicate
from bx_scholar_core.models.paper import Paper
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.sources import SEARCH_SOURCES, expand_sources
from bx_scholar_core.tools.registry import register_all_tools

OA_WORK = {
    "id": "https://openalex.org/W2967171639",
    "title": "Transcription regulation",
    "doi": "https://doi.org/10.1016/j.molcel.2019.07.008",
    "publication_year": 2019,
    "type": "article",
    "language": "en",
    "ids": {
        "openalex": "https://openalex.org/W2967171639",
        "pmid": "https://pubmed.ncbi.nlm.nih.gov/31398324",
        "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6789012",
    },
    "mesh": [
        {"descriptor_name": "DNA-Directed RNA Polymerases", "is_major_topic": False},
        {"descriptor_name": "Transcription, Genetic", "is_major_topic": True},
        {"descriptor_name": "DNA-Directed RNA Polymerases", "is_major_topic": False},
    ],
}


def _mock(client, handler) -> list[httpx.Request]:
    """Route a pooled client through a handler; returns the list of requests seen."""
    seen: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_h))
    return seen


def _server(tmp_path, **settings) -> tuple[FastMCP, ClientPool]:
    server = FastMCP("test")
    s = Settings(polite_email="ci@bxscholar.dev", cache_enabled=False, **settings)
    pool = register_all_tools(server, s, RankingService(data_dir=tmp_path))
    return server, pool


async def _call(server: FastMCP, tool: str, args: dict) -> dict:
    out = await server.call_tool(tool, args)
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


class TestParseWorkBiomedical:
    def test_pmid_pmcid_mesh_language(self) -> None:
        p = _parse_work(OA_WORK)
        assert p.pmid == "31398324"
        assert p.pmcid == "PMC6789012"
        assert p.language == "en"
        # major topic first, duplicates (one per MeSH qualifier) collapsed
        assert p.mesh == ["Transcription, Genetic", "DNA-Directed RNA Polymerases"]

    def test_missing_ids(self) -> None:
        p = _parse_work({"title": "x"})
        assert p.pmid == p.pmcid == ""
        assert p.mesh == []


class TestDedupPmid:
    def test_same_pmid_without_doi_merged(self) -> None:
        a = Paper(title="Alpha study", pmid="123")
        b = Paper(title="Completely different wording", pmid="123", abstract="richer")
        out = deduplicate([a, b])
        assert len(out) == 1
        assert out[0].abstract == "richer"


class TestExpandSources:
    def test_preset_and_order(self) -> None:
        names, unknown = expand_sources("openalex, br")
        assert names[0] == "openalex"
        assert "scielo" in names
        assert unknown == []

    def test_duplicates_collapsed(self) -> None:
        names, _ = expand_sources("scielo,br,scielo")
        assert names.count("scielo") == 1

    def test_unknown_reported(self) -> None:
        names, unknown = expand_sources("openalex,googlescholar")
        assert names == ["openalex"]
        assert unknown == ["googlescholar"]

    def test_every_registered_source_is_named_consistently(self) -> None:
        for name, source in SEARCH_SOURCES.items():
            assert source.name == name


class TestClientPool:
    def test_one_instance_per_client(self) -> None:
        pool = ClientPool(Settings(polite_email="ci@bxscholar.dev"))
        assert pool.openalex is pool.openalex
        assert isinstance(pool.openalex, OpenAlexClient)

    async def test_tool_calls_share_client_and_limiter(self, tmp_path) -> None:
        """Before the pool every call built a new client, so each got its own rate budget."""
        server, pool = _server(tmp_path)
        client = pool.openalex
        limiter = client._limiter
        _mock(client, lambda r: httpx.Response(200, json={"results": [], "meta": {"count": 0}}))

        for _ in range(2):
            await _call(server, "search_papers", {"query": "x", "sources": "openalex"})

        assert pool.openalex is client
        assert client._limiter is limiter
        assert sum(isinstance(c, OpenAlexClient) for c in pool._created) == 1
        await pool.aclose()


class TestSearchPapers:
    async def test_failing_source_is_reported_not_swallowed(self, tmp_path) -> None:
        server, pool = _server(tmp_path)
        _mock(
            pool.openalex,
            lambda r: httpx.Response(200, json={"results": [OA_WORK], "meta": {"count": 1}}),
        )
        _mock(pool.crossref, lambda r: httpx.Response(404))

        r = await _call(server, "search_papers", {"query": "x", "sources": "openalex,crossref"})

        assert r["returned"] == 1
        assert r["per_source"] == {"openalex": 1}
        assert "crossref" in r["errors"]
        await pool.aclose()

    async def test_source_without_key_is_skipped(self, tmp_path) -> None:
        server, pool = _server(tmp_path)
        _mock(pool.openalex, lambda r: httpx.Response(200, json={"results": [], "meta": {}}))

        r = await _call(server, "search_papers", {"query": "x", "sources": "openalex,tavily"})

        assert r["skipped"] == {"tavily": "TAVILY_API_KEY not set"}
        await pool.aclose()

    async def test_unknown_source_listed(self, tmp_path) -> None:
        server, pool = _server(tmp_path)
        _mock(pool.openalex, lambda r: httpx.Response(200, json={"results": [], "meta": {}}))

        r = await _call(server, "search_papers", {"query": "x", "sources": "openalex,nope"})

        assert r["unknown_sources"] == ["nope"]
        await pool.aclose()

    async def test_tavily_results_become_papers(self, tmp_path) -> None:
        """Tavily hits used to be counted and then dropped from the response."""
        server, pool = _server(tmp_path, tavily_api_key="k")
        hit = {"title": "Policy report", "url": "https://gov.example/r", "content": "summary"}
        _mock(pool.tavily, lambda r: httpx.Response(200, json={"results": [hit]}))

        r = await _call(server, "search_papers", {"query": "x", "sources": "tavily"})

        assert r["results"][0]["landing_url"] == "https://gov.example/r"
        assert r["results"][0]["source_type"] == "web"
        await pool.aclose()

    def test_description_lists_sources_and_presets(self, tmp_path) -> None:
        server, _ = _server(tmp_path)
        tool = next(t for t in server._tool_manager.list_tools() if t.name == "search_papers")
        assert "semantic_scholar" in tool.description
        assert "br=" in tool.description


class TestSciELO:
    async def test_uses_doi_prefix_filter(self, tmp_path) -> None:
        pool = ClientPool(Settings(polite_email="ci@bxscholar.dev"))
        seen = _mock(
            pool.scielo,
            lambda r: httpx.Response(200, json={"results": [OA_WORK], "meta": {"count": 1}}),
        )

        papers = await pool.scielo.search("mobilidade", year_from=2020, year_to=2024)

        flt = parse_qs(urlparse(str(seen[0].url)).query)["filter"][0]
        assert "doi_starts_with:10.1590" in flt
        assert "host_venue" not in flt
        assert "publication_year:>2019" in flt
        assert parse_qs(urlparse(str(seen[0].url)).query)["sort"] == ["relevance_score:desc"]
        # OA_WORK carries no open_access block: the paper is not claimed open
        assert papers[0].is_open_access is False
        await pool.aclose()

    async def test_open_access_comes_from_record(self) -> None:
        pool = ClientPool(Settings(polite_email="ci@bxscholar.dev"))
        work = {**OA_WORK, "open_access": {"is_oa": True, "oa_url": "https://x/y.pdf"}}
        _mock(pool.scielo, lambda r: httpx.Response(200, json={"results": [work]}))
        papers = await pool.scielo.search("x")
        assert papers[0].is_open_access is True
        assert papers[0].pdf_url == "https://x/y.pdf"
        await pool.aclose()


class TestRelevanceAndPortuguese:
    async def test_search_papers_defaults_to_relevance(self, tmp_path) -> None:
        server, pool = _server(tmp_path)
        seen = _mock(pool.openalex, lambda r: httpx.Response(200, json={"results": []}))
        await _call(server, "search_papers", {"query": "x", "sources": "openalex"})
        assert parse_qs(urlparse(str(seen[0].url)).query)["sort"] == ["relevance_score:desc"]
        await pool.aclose()

    async def test_pt_source_filters_language(self, tmp_path) -> None:
        server, pool = _server(tmp_path)
        seen = _mock(
            pool.openalex, lambda r: httpx.Response(200, json={"results": [OA_WORK], "meta": {}})
        )
        r = await _call(server, "search_papers", {"query": "x", "sources": "pt", "year_from": 2020})
        flt = parse_qs(urlparse(str(seen[0].url)).query)["filter"][0]
        assert "language:pt" in flt
        assert "publication_year:>2019" in flt
        assert r["results"][0]["source_api"] == "openalex_pt"
        await pool.aclose()

    async def test_error_propagates(self) -> None:
        """It used to fall back to a 403 endpoint and return [] silently."""
        from bx_scholar_core.clients.base import NonRetryableHTTPError

        pool = ClientPool(Settings(polite_email="ci@bxscholar.dev"))
        _mock(pool.scielo, lambda r: httpx.Response(400))
        with pytest.raises(NonRetryableHTTPError):
            await pool.scielo.search("x")
        await pool.aclose()


class TestDedupCodexRegression:
    def test_copy_without_doi_merges_through_pmid(self) -> None:
        with_doi = Paper(title="Same", year=2020, doi="10.1234/x", pmid="123")
        pmid_only = Paper(title="Same", year=2020, pmid="123")
        assert len(deduplicate([with_doi, pmid_only])) == 1
        assert len(deduplicate([pmid_only, with_doi])) == 1

    def test_record_bridging_two_groups_merges_them(self) -> None:
        a = Paper(title="A", doi="10.1234/a")
        b = Paper(title="B", pmid="9")
        bridge = Paper(title="C", doi="10.1234/a", pmid="9")
        assert len(deduplicate([a, b, bridge])) == 1
