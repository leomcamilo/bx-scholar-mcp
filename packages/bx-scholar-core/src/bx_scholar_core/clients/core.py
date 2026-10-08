"""CORE client — open-access outputs aggregated from repositories worldwide.

Search and DOI lookup work without a key. ``downloadUrl`` (a PDF CORE hosts
or links) comes either way; the extracted ``fullText`` only with a free API
key (https://core.ac.uk/services/api): without one CORE answers "Not
available for public API users." in its place.
"""

from __future__ import annotations

from typing import Any

from bx_scholar_core.clients.base import AsyncHTTPClient
from bx_scholar_core.id_resolver import is_valid_doi
from bx_scholar_core.models.paper import MAX_AUTHORS, Author, Paper, SourceType

CORE_BASE = "https://api.core.ac.uk/v3"
# What CORE puts in fullText for requests without a key.
HIDDEN_FULLTEXT = "Not available for public API users."

_DOC_TYPES: dict[str, SourceType] = {
    "research": "peer_reviewed",
    "thesis": "thesis",
    "slides": "unknown",
}


def _author(a: dict[str, Any]) -> Author:
    """Repositories write "Last, First"; keep that split. "First Last" stays whole."""
    name = (a.get("name") or "").strip()
    last, sep, first = name.partition(",")
    if sep and last.strip() and first.strip():
        return Author(
            name=f"{first.strip()} {last.strip()}",
            family=last.strip(),
            given=first.strip(),
            structure_source="source",
        )
    return Author(name=name)


def fulltext_of(work: dict[str, Any]) -> str:
    text = (work.get("fullText") or "").strip()
    return "" if text == HIDDEN_FULLTEXT else text


def parse_work(work: dict[str, Any]) -> Paper:
    year = str(work.get("yearPublished") or "")
    language = (work.get("language") or {}).get("code") or ""
    journal = next((j.get("title") for j in work.get("journals") or [] if j.get("title")), "")
    issn = next(
        (
            i.split(":", 1)[-1]
            for j in work.get("journals") or []
            for i in j.get("identifiers") or []
            if str(i).lower().startswith("issn:")
        ),
        "",
    )
    core_id = str(work.get("id") or "")
    download = work.get("downloadUrl") or ""
    return Paper(
        title=(work.get("title") or "").strip(),
        doi=work.get("doi") or "",
        year=int(year) if year.isdigit() else None,
        authors=[_author(a) for a in (work.get("authors") or [])[:MAX_AUTHORS] if a.get("name")],
        authors_truncated=len(work.get("authors") or []) > MAX_AUTHORS,
        abstract=(work.get("abstract") or "").strip(),
        cited_by_count=work.get("citationCount") or 0,
        source_type=_DOC_TYPES.get(str(work.get("documentType") or "").lower(), "unknown"),
        journal=journal or "",
        issn=issn,
        arxiv_id=work.get("arxivId") or "",
        pmid=str(work.get("pubmedId") or ""),
        language=language if len(language) == 2 else "",
        is_open_access=bool(download),
        pdf_url=download,
        landing_url=f"https://core.ac.uk/works/{core_id}" if core_id else "",
        external_ids={"core": core_id} if core_id else {},
        source_api="core",
    )


def _year_clause(year_from: int | None, year_to: int | None) -> str:
    out = ""
    if year_from:
        out += f" AND yearPublished>={int(year_from)}"
    if year_to:
        out += f" AND yearPublished<={int(year_to)}"
    return out


class CoreClient(AsyncHTTPClient):
    """Client for the CORE API v3.

    Rate limit: CORE allows about 10 requests a minute (more with a key, but
    the per-minute cap is the binding one), so 10 per 60 s.
    """

    base_url = CORE_BASE
    rate_limit = 10.0
    max_rate_period = 60.0
    timeout = 60.0

    def __init__(self, api_key: str = "", user_agent: str = "", **kwargs: Any) -> None:
        super().__init__(user_agent=user_agent or "BX-Scholar/0.1.0", **kwargs)
        self.api_key = api_key

    def _extra_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def _search(
        self, q: str, limit: int, cache_policy: tuple[str, int] | None
    ) -> tuple[list[dict[str, Any]], int]:
        # trailing slash required: without it CORE answers 301
        resp = await self.get(
            "/search/works/", params={"q": q, "limit": limit}, cache_policy=cache_policy
        )
        data = resp.json()
        return data.get("results") or [], data.get("totalHits") or 0

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 25,
    ) -> tuple[list[Paper], int]:
        """Search CORE. Returns (papers, total_hits)."""
        works, total = await self._search(
            f"({query}){_year_clause(year_from, year_to)}",
            min(limit, 100),
            ("search_results", 3600),
        )
        return [parse_work(w) for w in works], total

    async def by_doi(self, doi: str) -> dict[str, Any] | None:
        """The raw CORE work for ``doi``, or None. The DOI is validated before it
        is quoted into the query, and the hit must carry the same DOI."""
        if not is_valid_doi(doi):
            raise ValueError(f"not a valid DOI: {doi!r}")
        # not cached: the same request returns fullText only once a key is set
        works, _ = await self._search(f'doi:"{doi}"', 5, None)
        return next((w for w in works if (w.get("doi") or "").lower() == doi.lower()), None)
