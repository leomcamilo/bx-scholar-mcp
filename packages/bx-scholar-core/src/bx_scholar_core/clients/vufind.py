"""VuFind REST API clients — BDTD, OasisBR (IBICT) and LA Referencia.

The three portals run VuFind and answer the same ``/api/v1/search`` schema, so
one client covers them; subclasses only set the host and the search handler.
Records rarely carry a DOI, so deduplication across them relies on title+year.
"""

from __future__ import annotations

import re
from typing import Any

from bx_scholar_core.clients.base import AsyncHTTPClient
from bx_scholar_core.languages import to_iso639_1
from bx_scholar_core.models.paper import MAX_AUTHORS, Author, Paper, SourceType

_FIELDS = (
    "id", "title", "authors", "publicationDates", "summary", "urls", "formats",
    "languages", "institutions", "cleanDoi", "country",
)  # fmt: skip

_FORMAT_TYPES: dict[str, SourceType] = {
    "masterThesis": "thesis",
    "doctoralThesis": "thesis",
    "bachelorThesis": "thesis",
    "article": "peer_reviewed",
    "conferenceObject": "conference",
    "book": "book",
    "bookPart": "book_chapter",
    "report": "report",
    "preprint": "preprint",
}

THESIS_FORMATS = ("masterThesis", "doctoralThesis")


def _person(name: str) -> str:
    """'Schmal, Dominic' -> 'Dominic Schmal'; names without a comma are kept."""
    last, sep, first = name.partition(",")
    return f"{first.strip()} {last.strip()}" if sep and first.strip() else name.strip()


def _author(name: str) -> Author:
    """VuFind stores persons as "Last, First"; keep the split it already made."""
    last, sep, first = name.partition(",")
    if sep and last.strip():
        return Author(
            name=_person(name), family=last.strip(), given=first.strip(), structure_source="source"
        )
    return Author(name=name.strip())


class VuFindClient(AsyncHTTPClient):
    """Base client; subclasses set ``base_url``, ``source_api`` and ``search_type``.

    Rate limit: these are public-sector portals on modest hardware, so 2 req/s.
    """

    source_api = "vufind"
    search_type = "AllFields"
    rate_limit = 2.0
    max_rate_period = 1.0
    timeout = 45.0  # IBICT answers slowly under load

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 25,
        formats: tuple[str, ...] = (),
    ) -> tuple[list[Paper], int]:
        """Search the portal. ``formats`` restricts to VuFind format values (OR-ed)."""
        params: list[tuple[str, str | int]] = [
            ("lookfor", query),
            ("type", self.search_type),
            ("limit", min(limit, 100)),
            *(("field[]", f) for f in _FIELDS),
            # "~" makes repeated filters on the same facet an OR instead of an AND
            *(("filter[]", f'~format:"{f}"') for f in formats),
        ]
        if year_from or year_to:
            params += [
                ("daterange[]", "publishDate"),
                ("publishDatefrom", year_from or 1000),
                ("publishDateto", year_to or 9999),
            ]
        resp = await self.get(
            "/api/v1/search", params=params, cache_policy=("search_results", 3600)
        )
        data = resp.json()
        if data.get("status") != "OK":
            raise RuntimeError(f"{self.source_api}: {data.get('statusMessage', 'search failed')}")
        papers = [self.parse_record(r) for r in data.get("records") or []]
        return papers, data.get("resultCount", 0)

    def parse_record(self, record: dict[str, Any]) -> Paper:
        authors = record.get("authors") or {}
        primary = list((authors.get("primary") or {}).keys())
        secondary = list((authors.get("secondary") or {}).keys())
        fmt = next(iter(record.get("formats") or []), "")
        dates = record.get("publicationDates") or []
        year = re.match(r"\d{4}", dates[0]) if dates else None
        urls = [u["url"] for u in record.get("urls") or [] if u.get("url")]
        source_type = _FORMAT_TYPES.get(fmt, "unknown")

        external_ids = {self.source_api: record.get("id", "")}
        for key, value in (
            ("format", fmt),
            ("institution", next(iter(record.get("institutions") or []), "")),
            ("country", record.get("country") or ""),
            # On theses VuFind's secondary authors are the advisors (orientadores)
            (
                "advisors",
                "; ".join(_person(n) for n in secondary) if source_type == "thesis" else "",
            ),
        ):
            if value:
                external_ids[key] = value

        return Paper(
            title=(record.get("title") or "").strip(),
            doi=record.get("cleanDoi") or "",
            year=int(year.group()) if year else None,
            authors=[_author(n) for n in primary[:MAX_AUTHORS]],
            abstract=next(iter(record.get("summary") or []), "").strip(),
            source_type=source_type,
            language=to_iso639_1(next(iter(record.get("languages") or []), "")),
            is_open_access=bool(urls),
            landing_url=urls[0] if urls else f"{self.base_url}/Record/{record.get('id', '')}",
            external_ids=external_ids,
            source_api=self.source_api,
        )


class BDTDClient(VuFindClient):
    """Biblioteca Digital Brasileira de Teses e Dissertações (IBICT)."""

    base_url = "https://bdtd.ibict.br/vufind"
    source_api = "bdtd"


class OasisBRClient(VuFindClient):
    """OasisBR — Brazilian open-access aggregator (IBICT): articles, theses, books."""

    base_url = "https://oasisbr.ibict.br/vufind"
    source_api = "oasisbr"


class LAReferenciaClient(VuFindClient):
    """LA Referencia — Latin American network of open-access repositories."""

    base_url = "https://www.lareferencia.info/vufind"
    source_api = "lareferencia"
    search_type = "AllField"  # this instance names the handler in the singular
