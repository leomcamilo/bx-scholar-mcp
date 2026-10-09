"""J-STAGE client — articles of Japanese scholarly societies (JST).

WebAPI service 3 (article search), Atom XML, no key. Each entry carries the
title, authors and journal in English and Japanese when the article has
both. The XML is third-party input, so it is parsed with defusedxml.
"""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET

from bx_scholar_core.clients.base import AsyncHTTPClient
from bx_scholar_core.clients.cinii import japanese_or_english
from bx_scholar_core.models.paper import MAX_AUTHORS, Author, Paper

JSTAGE_BASE = "https://api.jstage.jst.go.jp/searchapi/do"
_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}
# J-STAGE's status for a search with no hits; any other ERR_ is an error.
NO_RESULTS = "ERR_001"


class JStageError(RuntimeError):
    pass


def _text(el: Element | None, path: str) -> str:
    found = el.find(path, _NS) if el is not None else None
    return (found.text or "").strip() if found is not None else ""


def _names(entry: Element, lang: str) -> list[str]:
    return [
        (n.text or "").strip()
        for n in entry.findall(f"a:author/a:{lang}/a:name", _NS)
        if (n.text or "").strip()
    ]


def parse_entry(entry: Element) -> Paper | None:
    """An Atom entry as a Paper, in the article's own language: the Japanese
    title and names when it has them, else the English ones. The English
    title is kept in external_ids["title_en"]. None for an empty entry."""
    title_ja = _text(entry, "a:article_title/a:ja")
    title_en = _text(entry, "a:article_title/a:en")
    title = title_ja or title_en or _text(entry, "a:title")
    if not title:
        return None
    names = (_names(entry, "ja") if title_ja else []) or _names(entry, "en")
    year = _text(entry, "a:pubyear")
    external = {"jstage": _text(entry, "a:cdjournal")} if _text(entry, "a:cdjournal") else {}
    if title_ja and title_en:
        external["title_en"] = title_en
    link = _text(entry, "a:article_link/a:ja" if title_ja else "a:article_link/a:en")
    return Paper(
        title=title,
        doi=_text(entry, "prism:doi"),
        year=int(year) if year.isdigit() else None,
        authors=[Author(name=n) for n in names[:MAX_AUTHORS]],
        authors_truncated=len(names) > MAX_AUTHORS,
        source_type="peer_reviewed",
        journal=_text(entry, "a:material_title/a:ja" if title_ja else "a:material_title/a:en")
        or _text(entry, "a:material_title/a:en"),
        issn=_text(entry, "prism:issn") or _text(entry, "prism:eIssn"),
        language=japanese_or_english(title),
        landing_url=link or _text(entry, "a:id"),
        external_ids=external,
        source_api="jstage",
    )


def parse_feed(xml: str) -> tuple[list[Paper], int]:
    root = ET.fromstring(xml)
    status = _text(root, "a:result/a:status")
    if status == NO_RESULTS:
        return [], 0
    if status and status != "0":
        raise JStageError(f"J-STAGE error {status}: {_text(root, 'a:result/a:message')}")
    papers = [p for p in map(parse_entry, root.findall("a:entry", _NS)) if p]
    total = _text(root, "os:totalResults")
    return papers, int(total) if total.isdigit() else len(papers)


class JStageClient(AsyncHTTPClient):
    """Client for the J-STAGE WebAPI.

    Rate limit: JST publishes no figure; 1 req/s is a polite share.
    """

    base_url = ""
    rate_limit = 1.0
    max_rate_period = 1.0

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 25,
    ) -> tuple[list[Paper], int]:
        """Search J-STAGE articles. Returns (papers, total_hits)."""
        params: dict[str, Any] = {"service": 3, "keyword": query, "count": min(limit, 1000)}
        if year_from:
            params["pubyearfrom"] = int(year_from)
        if year_to:
            params["pubyearto"] = int(year_to)
        resp = await self.get(JSTAGE_BASE, params=params, cache_policy=("search_results", 3600))
        return parse_feed(resp.text)
