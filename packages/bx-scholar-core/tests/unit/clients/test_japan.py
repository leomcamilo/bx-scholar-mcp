"""CiNii Research and J-STAGE clients, and the "asia" preset.

Fixtures are real responses recorded on 2026-10-08:
- cinii_articles.json: /opensearch/articles, q="urban mobility", 2018-2022
- cinii_ja.json: /opensearch/articles, q="都市交通" (its first items have no title)
- cinii_ja_titled.json: q="都市交通 計画", 2015-2020
- jstage_search.xml: "urban mobility", 2018-2022 (English and bilingual entries)
- jstage_ja.xml: "都市交通"; jstage_none.xml: a query with no hits (ERR_001)
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar_core.clients.cinii import CiNiiClient, parse_item
from bx_scholar_core.clients.jstage import JStageClient, JStageError, parse_feed
from bx_scholar_core.config import Settings
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.registry import register_all_tools

FIXTURES = Path(__file__).parents[2] / "fixtures"


def _cinii(name: str) -> dict:
    return json.loads((FIXTURES / f"cinii_{name}.json").read_text())


def _jstage(name: str) -> str:
    return (FIXTURES / f"jstage_{name}.xml").read_text()


def _mock(client, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_h))
    return seen


def _params(req: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(req.url)).query)


class TestCiNii:
    def test_article_with_doi_issn_and_split_names(self) -> None:
        items = _cinii("articles")["items"]
        p = next(p for p in map(parse_item, items) if p and p.doi)
        assert p.source_api == "cinii"
        assert p.source_type == "peer_reviewed"
        assert p.year and p.issn and p.journal
        assert p.landing_url.startswith("https://cir.nii.ac.jp/crid/")
        assert p.external_ids["cinii"].isdigit()

    def test_comma_names_keep_their_split_in_both_scripts(self) -> None:
        from bx_scholar_core.clients.cinii import _author

        ja = _author("猪又, 稔")
        assert (ja.family, ja.given, ja.name, ja.structure_source) == (
            "猪又", "稔", "猪又 稔", "source",
        )  # fmt: skip
        en = _author("Inomata, Minoru")
        assert (en.family, en.given, en.name) == ("Inomata", "Minoru", "Minoru Inomata")
        assert _author("Qi Wang").structure_source == "none"

    def test_items_without_title_are_dropped(self) -> None:
        items = _cinii("ja")["items"]
        assert not items[0].get("title")
        assert parse_item(items[0]) is None

    def test_title_script_sets_language(self) -> None:
        ja = parse_item(_cinii("ja_titled")["items"][0])
        assert ja is not None
        assert ja.title == "交通まちづくり : 「立地適正化計画」時代の都市交通計画"
        assert (ja.language, ja.year, ja.doi) == ("ja", 2016, "")
        assert ja.external_ids["naid"]
        en = parse_item(_cinii("articles")["items"][0])
        assert en is not None and en.language == "en"

    async def test_search_query(self) -> None:
        client = CiNiiClient(appid="app")
        seen = _mock(client, lambda r: httpx.Response(200, json=_cinii("articles")))
        papers, total = await client.search("urban mobility", 2018, 2022, limit=500)
        assert seen[0].url.path == "/opensearch/articles"
        assert _params(seen[0]) == {
            "format": ["json"], "q": ["urban mobility"], "count": ["200"],  # capped
            "from": ["2018"], "until": ["2022"], "appid": ["app"],
        }  # fmt: skip
        assert total == 1593
        assert len(papers) == 4
        await client.close()

    async def test_untitled_entries_do_not_empty_a_small_page(self) -> None:
        client = CiNiiClient()
        seen = _mock(client, lambda r: httpx.Response(200, json=_cinii("ja_titled")))
        papers, _ = await client.search("都市交通 計画", limit=2)
        assert _params(seen[0])["count"] == ["20"]  # over-fetch: untitled ones are dropped
        assert len(papers) == 2
        await client.close()


class TestJStage:
    def test_bilingual_entry_uses_the_japanese_side(self) -> None:
        papers, total = parse_feed(_jstage("search"))
        assert total == 16
        p = next(p for p in papers if p.doi == "10.2322/jjsass.69.98")
        assert p.title == "空飛ぶクルマの離着陸場設計のための緊急着陸時衝撃荷重シミュレーション"
        assert p.language == "ja"
        assert [a.name for a in p.authors] == ["三原 裕介", "中野 冠"]
        assert p.journal == "日本航空宇宙学会論文集"
        assert p.external_ids["title_en"].startswith("Modelling and Simulation")
        assert p.landing_url.endswith("/-char/ja/")
        assert p.year == 2021 and p.issn == "1344-6460"

    def test_english_only_entry(self) -> None:
        papers, _ = parse_feed(_jstage("search"))
        p = papers[0]
        assert p.title.startswith("Spatiotemporal patterns of population mobility")
        assert p.language == "en"
        assert [a.name for a in p.authors] == ["Zhen Yang", "Weijun Gao"]
        assert "title_en" not in p.external_ids
        assert p.source_api == "jstage"

    def test_japanese_query(self) -> None:
        papers, total = parse_feed(_jstage("ja"))
        assert total == 83
        assert len(papers) == 3
        assert all(p.title for p in papers)

    def test_no_hits_is_empty_not_an_error(self) -> None:
        assert parse_feed(_jstage("none")) == ([], 0)

    def test_other_status_is_an_error(self) -> None:
        bad = _jstage("none").replace("ERR_001", "ERR_002")
        with pytest.raises(JStageError, match="ERR_002"):
            parse_feed(bad)

    def test_xml_entities_are_refused(self) -> None:
        """Third-party XML goes through defusedxml: an entity bomb is rejected."""
        from defusedxml import EntitiesForbidden

        bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><feed>&a;</feed>'
        with pytest.raises(EntitiesForbidden):
            parse_feed(bomb)

    async def test_search_query(self) -> None:
        client = JStageClient()
        seen = _mock(client, lambda r: httpx.Response(200, text=_jstage("search")))
        await client.search("urban mobility", 2018, 2022, limit=5)
        assert seen[0].url.host == "api.jstage.jst.go.jp"
        assert _params(seen[0]) == {
            "service": ["3"], "keyword": ["urban mobility"], "count": ["5"],
            "pubyearfrom": ["2018"], "pubyearto": ["2022"],
        }  # fmt: skip
        await client.close()


async def test_asia_preset_searches_both(tmp_path) -> None:
    server = FastMCP("t")
    pool = register_all_tools(
        server,
        Settings(polite_email="ci@bxscholar.dev", cache_enabled=False),
        RankingService(data_dir=tmp_path),
    )
    _mock(pool.cinii, lambda r: httpx.Response(200, json=_cinii("articles")))
    _mock(pool.jstage, lambda r: httpx.Response(200, text=_jstage("search")))
    out = await server.call_tool("search_papers", {"query": "urban mobility", "sources": "asia"})
    blocks = out[0] if isinstance(out, tuple) else out
    r = json.loads(blocks[0].text)
    await pool.aclose()
    assert {p["source_api"] for p in r["results"]} >= {"cinii", "jstage"}
    assert "errors" not in r
