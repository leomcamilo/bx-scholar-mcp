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

QUERIES = {"scielo": "mobilidade urbana"}  # default: "smart city mobility"


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
