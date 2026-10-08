"""Tests for the Europe PMC client and the get_fulltext tool.

Fixtures in tests/fixtures/europepmc_* are real API responses (fulltext XML
trimmed to the first paragraph per section), so the parser is tested against
the actual schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar_core.clients.europepmc import (
    EuropePMCClient,
    jats_to_sections,
    parse_record,
)
from bx_scholar_core.config import Settings
from bx_scholar_core.id_resolver import resolve_id
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.registry import register_all_tools

FIXTURES = Path(__file__).parents[2] / "fixtures"


def _json(name: str) -> dict:
    return json.loads((FIXTURES / f"europepmc_{name}.json").read_text())


def _first(name: str) -> dict:
    return _json(name)["resultList"]["result"][0]


def _client(handler) -> tuple[EuropePMCClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    client = EuropePMCClient()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_h))
    return client, seen


class TestParseRecord:
    def test_journal_article(self) -> None:
        p = parse_record(_first("search"))
        assert p.title == (
            "Neurochallenges in smart cities: state-of-the-art, perspectives, "
            "and research directions"
        )  # trailing period stripped
        assert p.doi == "10.3389/fnins.2024.1279668"
        assert p.pmid == "39744330"
        assert p.pmcid == "PMC11688368"
        assert p.year == 2024
        assert p.language == "en"
        assert p.is_open_access
        assert p.source_type == "peer_reviewed"
        assert p.journal == "Frontiers in neuroscience"
        assert p.authors[0].name == "Begüm Özkaynak"
        assert p.abstract and "<" not in p.abstract
        assert p.landing_url == "https://europepmc.org/article/MED/39744330"
        assert p.source_api == "europepmc"

    def test_mesh_major_topics_first(self) -> None:
        p = parse_record(_first("by_doi"))
        assert p.mesh
        assert len(p.mesh) == len(set(p.mesh))
        # "Ribonuclease III" is major through its qualifier, "Arabidopsis" is not
        assert p.mesh.index("Ribonuclease III") < p.mesh.index("Arabidopsis")

    def test_preprint(self) -> None:
        p = parse_record(_first("preprint"))
        assert p.source_type == "preprint"
        assert p.external_ids["europepmc"].startswith("PPR:")
        assert p.journal == "Open Res Europe"


class TestClient:
    async def test_search_adds_year_clause(self) -> None:
        client, seen = _client(lambda r: httpx.Response(200, json=_json("search")))
        papers, total = await client.search("smart city", year_from=2020, year_to=2024, limit=3)
        q = parse_qs(urlparse(str(seen[0].url)).query)
        assert q["query"] == ["(smart city) AND PUB_YEAR:[2020 TO 2024]"]
        assert q["resultType"] == ["core"]
        assert total == 1311
        assert len(papers) == 3
        await client.close()

    async def test_open_ended_year(self) -> None:
        client, seen = _client(lambda r: httpx.Response(200, json=_json("search")))
        await client.search("x", year_from=2020)
        assert "PUB_YEAR:[2020 TO 2999]" in parse_qs(urlparse(str(seen[0].url)).query)["query"][0]
        await client.close()

    async def test_lookup_by_pmid_restricts_to_medline(self) -> None:
        client, seen = _client(lambda r: httpx.Response(200, json=_json("by_doi")))
        p = await client.lookup("pmid", "31398324")
        q = parse_qs(urlparse(str(seen[0].url)).query)["query"][0]
        assert q == "EXT_ID:31398324 AND SRC:MED"  # quoted form matches nothing (verified live)
        assert p is not None
        await client.close()

    async def test_lookup_query_forms(self) -> None:
        import pytest

        client, seen = _client(lambda r: httpx.Response(200, json=_json("by_doi")))
        await client.lookup("pmcid", "pmc11688368")
        await client.lookup("doi", "10.1016/j.molcel.2019.07.008")
        queries = [parse_qs(urlparse(str(r.url)).query)["query"][0] for r in seen]
        assert queries == ["PMCID:PMC11688368", 'DOI:"10.1016/j.molcel.2019.07.008"']
        with pytest.raises(ValueError):
            await client.lookup("pmid", "1 OR 2")
        await client.close()

    async def test_citations_and_references(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            name = "citations" if req.url.path.endswith("/citations") else "references"
            return httpx.Response(200, json=_json(name))

        client, seen = _client(handler)
        cites = await client.citations("MED", "31398324", limit=3)
        refs = await client.references("MED", "31398324", limit=3)
        assert seen[0].url.path.endswith("/MED/31398324/citations")
        assert cites and cites[0].title and cites[0].pmid
        assert refs[0].title == "The DNMT3 family of mammalian de novo DNA methyltransferases"
        assert refs[0].year == 2011
        await client.close()

    async def test_fulltext_missing_returns_none(self) -> None:
        client, _ = _client(lambda r: httpx.Response(404))
        assert await client.fulltext_xml("PMC1") is None
        await client.close()


class TestJats:
    def test_sections(self) -> None:
        doc = jats_to_sections((FIXTURES / "europepmc_fulltext.xml").read_text())
        assert doc["title"].startswith("Neurochallenges in smart cities")
        assert len(doc["abstract"]) > 500
        headings = [s["heading"] for s in doc["sections"]]
        assert headings[0] == "1 Introduction"
        assert headings[-1] == "5 Concluding remarks"
        assert all(s["text"] for s in doc["sections"])

    def test_nested_section_title_kept_inline(self) -> None:
        xml = (
            "<article><front><article-meta><title-group><article-title>T</article-title>"
            "</title-group></article-meta></front><body><sec><title>Methods</title><p>a</p>"
            "<sec><title>Data</title><p>b</p></sec></sec></body></article>"
        )
        doc = jats_to_sections(xml)
        assert doc["sections"] == [{"heading": "Methods", "text": "a\n\n### Data\n\nb"}]

    def test_rejects_entity_expansion(self) -> None:
        """Third-party XML: a billion-laughs payload must not be expanded."""
        import pytest
        from defusedxml import EntitiesForbidden

        bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><article>&a;</article>'
        with pytest.raises(EntitiesForbidden):
            jats_to_sections(bomb)


class TestResolveIds:
    def test_pmcid_and_pmid(self) -> None:
        assert resolve_id("PMC11688368").id_type == "pmcid"
        assert resolve_id("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6789012/").value == (
            "PMC6789012"
        )
        assert resolve_id("pmid:31398324").value == "31398324"
        assert resolve_id("https://pubmed.ncbi.nlm.nih.gov/31398324/").id_type == "pmid"

    def test_bare_number_stays_unknown(self) -> None:
        assert resolve_id("31398324").id_type == "unknown"


class TestGetFulltextTool:
    def _server(self, tmp_path, handler):
        server = FastMCP("t")
        pool = register_all_tools(
            server,
            Settings(polite_email="ci@bxscholar.dev", cache_enabled=False),
            RankingService(data_dir=tmp_path),
        )
        pool.europepmc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        # CORE is the second full-text source; here it never has the paper
        empty = {"totalHits": 0, "results": []}
        pool.core._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=empty))
        )
        return server, pool

    async def _call(self, server, args) -> dict:
        out = await server.call_tool("get_fulltext", args)
        blocks = out[0] if isinstance(out, tuple) else out
        return json.loads(blocks[0].text)

    @staticmethod
    def _handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/fullTextXML"):
            return httpx.Response(200, text=(FIXTURES / "europepmc_fulltext.xml").read_text())
        return httpx.Response(200, json=_json("search"))

    async def test_doi_resolved_to_pmcid_then_sections(self, tmp_path) -> None:
        server, pool = self._server(tmp_path, self._handler)
        r = await self._call(server, {"identifier": "10.3389/fnins.2024.1279668"})
        assert r["available"] is True
        assert r["pmcid"] == "PMC11688368"
        assert len(r["sections"]) == 5
        assert r["omitted_sections"] == []
        await pool.aclose()

    async def test_section_filter(self, tmp_path) -> None:
        server, pool = self._server(tmp_path, self._handler)
        r = await self._call(server, {"identifier": "PMC11688368", "sections": "conclu,intro"})
        assert [s["heading"] for s in r["sections"]] == ["1 Introduction", "5 Concluding remarks"]
        assert len(r["headings"]) == 5  # full outline always returned
        await pool.aclose()

    async def test_max_chars_omits_but_lists_rest(self, tmp_path) -> None:
        server, pool = self._server(tmp_path, self._handler)
        r = await self._call(server, {"identifier": "PMC11688368", "max_chars": 1300})
        assert [s["heading"] for s in r["sections"]] == ["1 Introduction"]
        assert "5 Concluding remarks" in r["omitted_sections"]
        await pool.aclose()

    async def test_unavailable_points_to_pdf_path(self, tmp_path) -> None:
        empty = {"hitCount": 0, "resultList": {"result": []}}
        server, pool = self._server(tmp_path, lambda r: httpx.Response(200, json=empty))
        r = await self._call(server, {"identifier": "10.1234/closed"})
        assert r["available"] is False
        assert "download_pdf" in r["next_step"]
        await pool.aclose()

    async def test_rejects_unusable_identifier(self, tmp_path) -> None:
        server, pool = self._server(tmp_path, self._handler)
        r = await self._call(server, {"identifier": "W123"})
        assert "error" in r
        await pool.aclose()


class TestCodexReviewRegressions:
    """Findings from the Codex (gpt-6-astra) review of 2026-10-08."""

    async def test_doi_injection_rejected(self) -> None:
        import pytest

        client, seen = _client(lambda r: httpx.Response(200, json=_json("search")))
        with pytest.raises(ValueError):
            await client.lookup("doi", '10.9999/x" OR PMCID:PMC123 OR DOI:"10.9999/y')
        assert seen == []  # never reached the API
        await client.close()

    async def test_doi_with_parentheses_allowed(self) -> None:
        client, seen = _client(lambda r: httpx.Response(200, json={"resultList": {"result": []}}))
        await client.lookup("doi", "10.1016/S0140-6736(20)30183-5")
        assert parse_qs(urlparse(str(seen[0].url)).query)["query"] == [
            'DOI:"10.1016/S0140-6736(20)30183-5"'
        ]
        await client.close()

    async def test_lookup_rejects_hit_with_other_identifier(self) -> None:
        """Even if the query matched something else, a different paper is not returned."""
        client, _ = _client(lambda r: httpx.Response(200, json=_json("search")))
        assert await client.lookup("doi", "10.1234/not-in-results") is None
        await client.close()

    async def test_fulltext_403_raises_instead_of_unavailable(self) -> None:
        import pytest

        from bx_scholar_core.clients.base import NonRetryableHTTPError

        client, _ = _client(lambda r: httpx.Response(403))
        with pytest.raises(NonRetryableHTTPError):
            await client.fulltext_xml("PMC1")
        await client.close()

    async def test_tool_reports_blocked_source_as_error(self, tmp_path) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/fullTextXML"):
                return httpx.Response(403)
            return httpx.Response(200, json=_json("search"))

        t = TestGetFulltextTool()
        server, pool = t._server(tmp_path, handler)
        r = await t._call(server, {"identifier": "PMC11688368"})
        assert "available" not in r
        assert "403" in r["error"]
        await pool.aclose()

    async def test_max_chars_caps_first_section(self, tmp_path) -> None:
        t = TestGetFulltextTool()
        server, pool = t._server(tmp_path, TestGetFulltextTool._handler)
        r = await t._call(server, {"identifier": "PMC11688368", "max_chars": 100})
        assert sum(len(s["text"]) for s in r["sections"]) == 100
        assert r["truncated_section"] == "1 Introduction"
        assert "5 Concluding remarks" in r["omitted_sections"]
        await pool.aclose()


@pytest.mark.parametrize(
    ("doi", "valid"),
    [
        ("10.1000.10/123456", True),  # subdivided prefix (DOI Handbook)
        ("10.1016/S0140-6736(20)30183-5", True),
        ("10.1002/(SICI)1097-4571(199806)49:8<693::AID-ASI3>3.0.CO;2-0", True),
        ("10.1234/a#b", True),
        ('10.9999/x" OR PMCID:PMC123', False),
        ("10.1234/a b", False),
        ("10.1/x", False),
    ],
)
def test_doi_validation(doi: str, valid: bool) -> None:
    from bx_scholar_core.id_resolver import is_valid_doi

    assert is_valid_doi(doi) is valid
