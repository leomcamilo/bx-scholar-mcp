"""CiNii Research client — Japanese articles, books and dissertations (NII).

OpenSearch with JSON-LD output, no key required; an ``appid`` (free) is
accepted. The ``articles`` endpoint is used: ``all`` mixes in KAKEN research
projects and datasets, which are not papers.
"""

from __future__ import annotations

import re
from typing import Any

from bx_scholar_core.clients.base import AsyncHTTPClient
from bx_scholar_core.models.paper import MAX_AUTHORS, Author, Paper, SourceType

CINII_BASE = "https://cir.nii.ac.jp/opensearch"

_TAG = re.compile(r"<[^>]+>")
# Kana or kanji: the record is in Japanese (CiNii holds Japanese and English)
_JAPANESE = re.compile("[぀-ヿ㐀-䶿一-鿿]")

_TYPES: dict[str, SourceType] = {
    "article": "peer_reviewed",
    "book": "book",
    "dissertation": "thesis",
}


def japanese_or_english(text: str) -> str:
    return "ja" if _JAPANESE.search(text) else "en"


def _author(name: str) -> Author:
    """CiNii writes "猪又, 稔" or "Inomata, Minoru" (family first, comma) for
    some records and "Qi Wang" for others; only the comma form is split."""
    family, sep, given = (x.strip() for x in name.partition(","))
    if sep and family and given:
        # display order: family first in Japanese ("猪又 稔"), given first otherwise
        shown = f"{family} {given}" if _JAPANESE.search(family) else f"{given} {family}"
        return Author(name=shown, family=family, given=given, structure_source="source")
    return Author(name=name.strip())


def _identifiers(item: dict[str, Any]) -> dict[str, str]:
    """{"DOI": ..., "NAID": ..., "NCID": ...} from dc:identifier."""
    out: dict[str, str] = {}
    for ident in item.get("dc:identifier") or []:
        kind = str(ident.get("@type", "")).removeprefix("cir:")
        if kind and ident.get("@value") and kind not in out:
            out[kind] = str(ident["@value"])
    return out


def parse_item(item: dict[str, Any]) -> Paper | None:
    """A CiNii item as a Paper; None for items without a title (catalog
    entries of a book series carry only the series name)."""
    title = (item.get("title") or "").strip()
    if not title:
        return None
    ids = _identifiers(item)
    crid = str(item.get("@id") or "").rsplit("/", 1)[-1]
    date = str(item.get("prism:publicationDate") or "")
    external = {"cinii": crid} if crid else {}
    if "NAID" in ids:
        external["naid"] = ids["NAID"]
    creators = item.get("dc:creator") or []
    return Paper(
        title=title,
        doi=ids.get("DOI", ""),
        year=int(date[:4]) if date[:4].isdigit() else None,
        # Some records list each person twice, in Japanese and romanized
        # ("木田 勇輔", "Kida Yusuku"); there is no reliable way to pair them.
        authors=[_author(n) for n in creators[:MAX_AUTHORS] if str(n).strip()],
        authors_truncated=len(creators) > MAX_AUTHORS,
        abstract=_TAG.sub("", str(item.get("description") or "")).strip(),
        source_type=_TYPES.get(str(item.get("dc:type") or "").lower(), "unknown"),
        journal=str(item.get("prism:publicationName") or ""),
        issn=str(item.get("prism:issn") or ""),
        language=japanese_or_english(title),
        landing_url=str(item.get("@id") or ""),
        external_ids=external,
        source_api="cinii",
    )


class CiNiiClient(AsyncHTTPClient):
    """Client for CiNii Research OpenSearch.

    Rate limit: NII publishes no figure; 1 req/s is a polite share.
    """

    base_url = CINII_BASE
    rate_limit = 1.0
    max_rate_period = 1.0

    def __init__(self, appid: str = "", user_agent: str = "", **kwargs: Any) -> None:
        super().__init__(user_agent=user_agent or "BX-Scholar/0.1.0", **kwargs)
        self._appid = appid

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 25,
        endpoint: str = "articles",
    ) -> tuple[list[Paper], int]:
        """Search CiNii Research. Returns (papers, total_hits)."""
        # Untitled catalog entries are dropped after the fact and can fill the
        # first page ("都市交通" starts with three), so ask for more than needed.
        count = min(max(limit * 2, 20), 200)
        params: dict[str, Any] = {"format": "json", "q": query, "count": count}
        if year_from:
            params["from"] = int(year_from)
        if year_to:
            params["until"] = int(year_to)
        if self._appid:
            params["appid"] = self._appid
        resp = await self.get(f"/{endpoint}", params=params, cache_policy=("search_results", 3600))
        data = resp.json()
        papers = [p for p in map(parse_item, data.get("items") or []) if p]
        return papers[:limit], int(data.get("opensearch:totalResults") or 0)
