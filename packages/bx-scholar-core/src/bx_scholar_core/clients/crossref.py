"""CrossRef API client — DOI verification and bibliographic metadata."""

from __future__ import annotations

from typing import Any

from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError
from bx_scholar_core.models.paper import Author, Paper
from bx_scholar_core.models.verification import RetractionStatus

CROSSREF_BASE = "https://api.crossref.org"


def _parse_author(a: dict[str, Any]) -> Author:
    """Crossref gives persons as given/family and organizations as name."""
    if a.get("name") and not a.get("family"):
        return Author(
            name=a["name"], literal=a["name"], kind="organization", structure_source="source"
        )
    family, given = (a.get("family") or "").strip(), (a.get("given") or "").strip()
    return Author(
        name=f"{given} {family}".strip(),
        family=family,
        given=given,
        orcid=(a.get("ORCID") or "")
        .replace("http://orcid.org/", "")
        .replace("https://orcid.org/", ""),
        structure_source="source" if family else "none",
    )


def _parse_item(item: dict[str, Any]) -> Paper:
    """Parse a CrossRef item into a canonical Paper."""
    pub_date = (
        item.get("published-print")
        or item.get("published-online")
        or item.get("published")
        or item.get("issued")
        or {}
    )
    date_parts = pub_date.get("date-parts", [[None]])
    year = date_parts[0][0] if date_parts and date_parts[0] else None

    work_type = item.get("type", "")
    if work_type == "journal-article":
        source_type = "peer_reviewed"
    elif work_type == "book":
        source_type = "book"
    elif work_type == "book-chapter":
        source_type = "book_chapter"
    elif work_type in ("proceedings-article", "proceedings"):
        source_type = "conference"
    elif work_type == "posted-content":
        source_type = "preprint"
    elif work_type == "dissertation":
        source_type = "thesis"
    else:
        source_type = "unknown"

    return Paper(
        subtitle=(item.get("subtitle") or [""])[0],
        title=(item.get("title") or [""])[0],
        doi=item.get("DOI", ""),
        year=year,
        # Crossref returns the whole list; keep it so a co-author past the 100th
        # can be verified (search output trims it, see tools/search.py)
        authors=[_parse_author(a) for a in item.get("author") or []],
        cited_by_count=item.get("is-referenced-by-count", 0),
        journal=(item.get("container-title") or [""])[0],
        issn=(item.get("ISSN") or [""])[0],
        source_type=source_type,
        source_api="crossref",
    )


class CrossRefClient(AsyncHTTPClient):
    """Client for the CrossRef API.

    Rate limit: 50 req/s with polite email.
    """

    base_url = CROSSREF_BASE
    rate_limit = 50.0
    max_rate_period = 1.0

    def __init__(self, polite_email: str, user_agent: str = "", **kwargs) -> None:
        ua = user_agent or f"BX-Scholar/0.1.0 (mailto:{polite_email})"
        super().__init__(user_agent=ua, **kwargs)

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        journal_name: str | None = None,
        sort: str = "is-referenced-by-count",
        rows: int = 25,
    ) -> tuple[list[Paper], int]:
        """Search for papers. Returns (papers, total_count)."""
        params: dict[str, Any] = {
            "query": query,
            "sort": sort,
            "order": "desc",
            "rows": min(rows, 50),
        }
        filters: list[str] = []
        if year_from:
            filters.append(f"from-pub-date:{year_from}")
        if year_to:
            filters.append(f"until-pub-date:{year_to}")
        if journal_name:
            params["query.container-title"] = journal_name
        if filters:
            params["filter"] = ",".join(filters)

        resp = await self.get("/works", params=params, cache_policy=("search_results", 3600))
        data = resp.json()
        items = data.get("message", {}).get("items", [])
        total = data.get("message", {}).get("total-results", 0)
        return [_parse_item(i) for i in items], total

    async def get_work(self, doi: str) -> Paper | None:
        """Get a single paper by DOI."""
        try:
            resp = await self.get(f"/works/{doi}", cache_policy=("paper_metadata", 7 * 86400))
            item = resp.json().get("message", {})
            return _parse_item(item)
        except NonRetryableHTTPError:
            return None

    async def search_bibliographic(self, text: str, rows: int = 10) -> list[Paper]:
        """Candidates for a citation string (author + title), ranked by Crossref.

        No year filter: a hard ±1 filter would hide the very record needed to
        show a year conflict. Deciding which candidate (if any) is the cited
        work is the verifier's job, not this method's. HTTP errors propagate.
        """
        resp = await self.get(
            "/works",
            params={"query.bibliographic": text, "rows": min(rows, 20)},
            cache_policy=("verification", 86400),
        )
        return [_parse_item(i) for i in resp.json().get("message", {}).get("items", [])]

    async def check_retraction(self, doi: str) -> RetractionStatus:
        """Check if a paper has been retracted."""
        try:
            resp = await self.get(f"/works/{doi}", cache_policy=("verification", 86400))
            item = resp.json().get("message", {})
            updates = item.get("update-to") or []
            retracted = any(u.get("type") == "retraction" for u in updates)
            return RetractionStatus(
                doi=doi,
                retracted=retracted,
                is_retraction_notice=item.get("type") == "retraction",
                title=(item.get("title") or [""])[0],
                updates=[{"type": u.get("type", ""), "doi": u.get("DOI", "")} for u in updates],
            )
        except NonRetryableHTTPError:
            return RetractionStatus(doi=doi, error=f"Could not fetch DOI: {doi}")
