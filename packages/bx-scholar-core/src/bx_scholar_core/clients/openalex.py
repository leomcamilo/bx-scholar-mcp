"""OpenAlex API client — 250M+ academic papers."""

from __future__ import annotations

from typing import Any

from bx_scholar_core.clients.base import AsyncHTTPClient
from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.paper import Author, Paper

logger = get_logger(__name__)

OPENALEX_BASE = "https://api.openalex.org"


def _reconstruct_abstract(inverted_index: dict[str, list[int]]) -> str:
    """Reconstruct abstract from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def _parse_work(work: dict[str, Any]) -> Paper:
    """Parse an OpenAlex work dict into a canonical Paper model."""
    source = (work.get("primary_location") or {}).get("source") or {}
    doi_raw = work.get("doi") or ""

    authors = [
        Author(
            name=a.get("author", {}).get("display_name", ""),
            openalex_id=(a.get("author", {}).get("id") or "").replace("https://openalex.org/", ""),
        )
        for a in (work.get("authorships") or [])[:10]
    ]

    work_type = work.get("type", "")
    if work_type == "article":
        source_type = "peer_reviewed"
    elif work_type in ("preprint", "posted-content"):
        source_type = "preprint"
    elif work_type == "book":
        source_type = "book"
    elif work_type == "book-chapter":
        source_type = "book_chapter"
    elif work_type in ("proceedings-article", "proceedings"):
        source_type = "conference"
    elif work_type == "dissertation":
        source_type = "thesis"
    else:
        source_type = "unknown"

    return Paper(
        title=work.get("title") or "",
        doi=doi_raw,
        year=work.get("publication_year"),
        authors=authors,
        abstract=_reconstruct_abstract(work.get("abstract_inverted_index") or {}),
        cited_by_count=work.get("cited_by_count", 0),
        source_type=source_type,
        journal=source.get("display_name") or "",
        issn=source.get("issn_l") or "",
        openalex_id=(work.get("id") or "").replace("https://openalex.org/", ""),
        is_open_access=(work.get("open_access") or {}).get("is_oa", False),
        source_api="openalex",
        references=[
            r.replace("https://openalex.org/", "") for r in (work.get("referenced_works") or [])
        ],
    )


class OpenAlexClient(AsyncHTTPClient):
    """Client for the OpenAlex API.

    Rate limit: 10 req/s with polite email (vs 1 req/s without).
    """

    base_url = OPENALEX_BASE
    rate_limit = 10.0
    max_rate_period = 1.0

    def __init__(self, polite_email: str, user_agent: str = "") -> None:
        ua = user_agent or f"BX-Scholar/0.1.0 (mailto:{polite_email})"
        super().__init__(user_agent=ua)
        self._polite_email = polite_email

    def _default_params(self) -> dict[str, str]:
        return {"mailto": self._polite_email}

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        journal_issn: str | None = None,
        type_filter: str | None = None,
        sort: str = "cited_by_count:desc",
        per_page: int = 25,
    ) -> tuple[list[Paper], int]:
        """Search for papers. Returns (papers, total_count)."""
        params: dict[str, Any] = {
            **self._default_params(),
            "search": query,
            "sort": sort,
            "per_page": min(per_page, 50),
        }
        filters: list[str] = []
        if year_from:
            filters.append(f"publication_year:>{year_from - 1}")
        if year_to:
            filters.append(f"publication_year:<{year_to + 1}")
        if journal_issn:
            filters.append(f"primary_location.source.issn:{journal_issn}")
        if type_filter:
            filters.append(f"type:{type_filter}")
        if filters:
            params["filter"] = ",".join(filters)

        resp = await self.get("/works", params=params)
        data = resp.json()
        papers = [_parse_work(w) for w in data.get("results", [])]
        total = data.get("meta", {}).get("count", 0)
        return papers, total

    async def get_work(self, doi: str) -> Paper | None:
        """Get a single paper by DOI."""
        from bx_scholar_core.clients.base import NonRetryableHTTPError

        try:
            resp = await self.get(
                f"/works/https://doi.org/{doi}",
                params=self._default_params(),
            )
            return _parse_work(resp.json())
        except NonRetryableHTTPError:
            return None

    async def get_citations(
        self,
        doi: str,
        direction: str = "citing",
        per_page: int = 25,
    ) -> list[Paper]:
        """Get citing papers or references for a DOI."""
        from bx_scholar_core.clients.base import NonRetryableHTTPError

        try:
            # First resolve DOI to OpenAlex ID
            resp_meta = await self.get(
                f"/works/https://doi.org/{doi}",
                params={
                    **self._default_params(),
                    "select": "id,referenced_works,cited_by_api_url",
                },
            )
            work_meta = resp_meta.json()
            openalex_id = (work_meta.get("id") or "").replace("https://openalex.org/", "")

            if direction == "citing":
                cited_by_url = work_meta.get("cited_by_api_url", "")
                if cited_by_url:
                    resp = await self.get(
                        cited_by_url,
                        params={
                            "per_page": min(per_page, 50),
                            "sort": "cited_by_count:desc",
                            "mailto": self._polite_email,
                        },
                    )
                else:
                    resp = await self.get(
                        "/works",
                        params={
                            **self._default_params(),
                            "filter": f"cites:{openalex_id}",
                            "per_page": min(per_page, 50),
                            "sort": "cited_by_count:desc",
                        },
                    )
            else:
                ref_ids = (work_meta.get("referenced_works") or [])[:per_page]
                if not ref_ids:
                    return []
                pipe = "|".join(ref_ids)
                resp = await self.get(
                    "/works",
                    params={
                        **self._default_params(),
                        "filter": f"openalex_id:{pipe}",
                        "per_page": min(per_page, 50),
                    },
                )

            return [_parse_work(w) for w in resp.json().get("results", [])]
        except NonRetryableHTTPError:
            return []

    async def get_author(self, name: str, per_page: int = 20) -> dict[str, Any] | None:
        """Search for an author by name and return their profile + works."""
        from bx_scholar_core.clients.base import NonRetryableHTTPError

        try:
            resp = await self.get(
                "/authors",
                params={**self._default_params(), "search": name},
            )
            authors = resp.json().get("results", [])
            if not authors:
                return None
            author = authors[0]
            author_id = author["id"]

            resp_works = await self.get(
                "/works",
                params={
                    **self._default_params(),
                    "filter": f"author.id:{author_id}",
                    "sort": "cited_by_count:desc",
                    "per_page": min(per_page, 50),
                },
            )
            works = [_parse_work(w) for w in resp_works.json().get("results", [])]
            return {
                "author": Author(
                    name=author.get("display_name", ""),
                    openalex_id=author_id.replace("https://openalex.org/", ""),
                    h_index=(author.get("summary_stats") or {}).get("h_index"),
                    works_count=author.get("works_count"),
                    cited_by_count=author.get("cited_by_count"),
                ),
                "works": works,
            }
        except NonRetryableHTTPError:
            return None

    async def get_source(self, issn_or_name: str) -> dict[str, Any] | None:
        """Get journal/source metadata from OpenAlex."""
        from bx_scholar_core.clients.base import NonRetryableHTTPError

        if "-" in issn_or_name and len(issn_or_name) <= 10:
            params = {**self._default_params(), "filter": f"issn:{issn_or_name}"}
        else:
            params = {**self._default_params(), "search": issn_or_name}

        try:
            resp = await self.get("/sources", params=params)
            sources = resp.json().get("results", [])
            if not sources:
                return None
            return sources[0]
        except NonRetryableHTTPError:
            return None

    async def get_keyword_counts(
        self, keyword: str, year_from: int = 2015, year_to: int = 2025
    ) -> dict[int, int]:
        """Get publication counts per year for a keyword."""
        counts: dict[int, int] = {}
        for year in range(year_from, year_to + 1):
            try:
                resp = await self.get(
                    "/works",
                    params={
                        **self._default_params(),
                        "filter": f"default.search:{keyword},publication_year:{year}",
                        "per_page": 1,
                    },
                )
                counts[year] = resp.json().get("meta", {}).get("count", 0)
            except Exception:
                counts[year] = 0
        return counts
