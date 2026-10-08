"""Live smoke tests against the real APIs. Opt-in: `pytest -m live`.

They catch what mocks can't: an API changing its schema or rejecting a filter
(as OpenAlex did with host_venue, which silently emptied SciELO results).
Sources whose API key is missing are skipped.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar_core.config import Settings
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.sources import SEARCH_SOURCES
from bx_scholar_core.tools.registry import register_all_tools

pytestmark = pytest.mark.live

QUERIES = {  # default: "smart city mobility"
    "scielo": "mobilidade urbana",
    "bdtd": "mobilidade urbana",
    "oasisbr": "mobilidade urbana",
    "lareferencia": "movilidad urbana",
    "cinii": "都市交通",
    "jstage": "都市交通",
}


@pytest.fixture
def server_and_pool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # keep a developer .env from leaking keys in
    import os

    settings = Settings(
        polite_email=os.environ.get("POLITE_EMAIL", "bx-scholar-ci@users.noreply.github.com"),
        cache_enabled=False,
    )
    server = FastMCP("live")
    pool = register_all_tools(server, settings, RankingService(data_dir=tmp_path))
    return server, pool


@pytest.mark.parametrize("source", list(SEARCH_SOURCES))
async def test_source_returns_papers(server_and_pool, source) -> None:
    server, pool = server_and_pool
    if SEARCH_SOURCES[source].missing_config(pool):
        pytest.skip(f"{source}: API key not configured")
    out = await server.call_tool(
        "search_papers",
        {"query": QUERIES.get(source, "smart city mobility"), "sources": source, "per_page": 5},
    )
    blocks = out[0] if isinstance(out, tuple) else out
    r = json.loads(blocks[0].text)
    await pool.aclose()

    if "429" in str(r.get("errors", {}).get(source, "")):
        pytest.skip(f"{source}: rate limited by the API (not a schema break)")
    assert "errors" not in r, r.get("errors")
    assert r["returned"] > 0
    assert r["results"][0].get("title")


async def test_openalex_exposes_pmid_and_mesh(server_and_pool) -> None:
    _, pool = server_and_pool
    paper = await pool.openalex.get_work("10.1016/j.molcel.2019.07.008")
    await pool.aclose()
    assert paper is not None
    assert paper.pmid == "31398324"
    assert paper.mesh


async def test_get_fulltext_from_doi(server_and_pool) -> None:
    server, pool = server_and_pool
    out = await server.call_tool(
        "get_fulltext", {"identifier": "10.3389/fnins.2024.1279668", "max_chars": 5000}
    )
    blocks = out[0] if isinstance(out, tuple) else out
    r = json.loads(blocks[0].text)
    await pool.aclose()
    assert r["available"] is True
    assert r["pmcid"] == "PMC11688368"
    assert len(r["headings"]) >= 5
    assert r["sections"]


async def test_get_paper_by_pmcid_and_pmid(server_and_pool) -> None:
    server, pool = server_and_pool
    results = []
    for ident in ("PMC11688368", "pmid:31398324"):
        out = await server.call_tool("get_paper", {"identifier": ident})
        blocks = out[0] if isinstance(out, tuple) else out
        results.append(json.loads(blocks[0].text))
    await pool.aclose()
    assert results[0]["paper"]["doi"] == "10.3389/fnins.2024.1279668"
    assert results[1]["paper"]["pmid"] == "31398324"


async def test_search_theses_latam_doctoral(server_and_pool) -> None:
    server, pool = server_and_pool
    out = await server.call_tool(
        "search_theses",
        {
            "query": "mobilidade urbana",
            "scope": "latam",
            "degree": "doctoral",
            "year_from": 2018,
            "per_page": 5,
        },
    )
    blocks = out[0] if isinstance(out, tuple) else out
    r = json.loads(blocks[0].text)
    await pool.aclose()
    assert "errors" not in r, r.get("errors")
    assert set(r["per_source"]) == {"bdtd", "lareferencia"}
    assert all(p["external_ids"]["format"] == "doctoralThesis" for p in r["results"])
    assert all(p["year"] >= 2018 for p in r["results"] if p.get("year"))


async def test_get_citations_merges_openalex_and_opencitations(server_and_pool) -> None:
    server, pool = server_and_pool
    out = await server.call_tool(
        "get_citations",
        {"identifier": "10.1016/j.giq.2019.06.002", "per_page": 10},
    )
    blocks = out[0] if isinstance(out, tuple) else out
    r = json.loads(blocks[0].text)
    await pool.aclose()
    assert "errors" not in r, r.get("errors")
    assert r["per_source"]["openalex"] > 0
    assert r["total_links"]["openalex"] > 100
    assert r["total_links"]["opencitations"] > 100
    assert all(p.get("external_ids", {}).get("citation_sources") for p in r["results"])


async def test_snowball_from_openalex_id(server_and_pool) -> None:
    """Seeds given as OpenAlex IDs used to resolve to nothing."""
    server, pool = server_and_pool
    out = await server.call_tool(
        "snowball",
        {"seed_identifiers": "W2955156490", "direction": "references", "max_papers": 20},
    )
    blocks = out[0] if isinstance(out, tuple) else out
    r = json.loads(blocks[0].text)
    await pool.aclose()
    assert r["seeds"] == ["10.1016/j.giq.2019.06.002"]
    assert r["stats"]["total_papers"] > 0


async def test_core_lookup_by_doi_has_a_pdf(server_and_pool) -> None:
    """Without a key CORE hides fullText but still gives the PDF it hosts."""
    _, pool = server_and_pool
    work = await pool.core.by_doi("10.1371/journal.pone.0185809")
    await pool.aclose()
    assert work is not None
    assert work["downloadUrl"].startswith("https://core.ac.uk/download/")
