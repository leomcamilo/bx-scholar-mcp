"""SciELO client — Brazilian/LATAM Open Access papers.

Uses OpenAlex with SciELO publisher filter as primary strategy,
with direct SciELO search API as fallback.
"""

from __future__ import annotations

from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError
from bx_scholar_core.clients.openalex import _parse_work
from bx_scholar_core.models.paper import Author, Paper

SCIELO_SEARCH = "https://search.scielo.org/"


class SciELOClient(AsyncHTTPClient):
    """Client for SciELO via OpenAlex filter.

    Rate limit: 5 req/s.
    All SciELO papers are Open Access.
    """

    base_url = ""
    rate_limit = 5.0
    max_rate_period = 1.0

    def __init__(self, polite_email: str, user_agent: str = "") -> None:
        ua = user_agent or f"BX-Scholar/0.1.0 (mailto:{polite_email})"
        super().__init__(user_agent=ua)
        self._polite_email = polite_email

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        max_results: int = 20,
    ) -> list[Paper]:
        """Search SciELO papers via OpenAlex SciELO filter."""
        oa_filter = "host_venue.publisher:SciELO"
        if year_from:
            oa_filter += f",publication_year:>{year_from - 1}"
        if year_to:
            oa_filter += f",publication_year:<{year_to + 1}"

        try:
            resp = await self.get(
                "https://api.openalex.org/works",
                params={
                    "search": query,
                    "filter": oa_filter,
                    "per_page": min(max_results, 50),
                    "mailto": self._polite_email,
                },
            )
            data = resp.json()
            papers: list[Paper] = []
            for work in data.get("results", []):
                p = _parse_work(work)
                p.source_api = "scielo_via_openalex"
                p.is_open_access = True
                oa_url = (work.get("open_access") or {}).get("oa_url")
                if oa_url:
                    p.pdf_url = oa_url
                papers.append(p)
            return papers
        except (NonRetryableHTTPError, Exception):
            return await self._search_direct(query, max_results)

    async def _search_direct(self, query: str, max_results: int) -> list[Paper]:
        """Fallback: search SciELO directly."""
        try:
            resp = await self.get(
                SCIELO_SEARCH,
                params={"q": query, "output": "json", "count": min(max_results, 50), "lang": "en"},
            )
            if "application/json" not in resp.headers.get("content-type", ""):
                return []

            data = resp.json()
            papers: list[Paper] = []
            for doc in (data.get("docs") or data.get("results") or [])[:max_results]:
                title = (
                    doc.get("title", [""])[0]
                    if isinstance(doc.get("title"), list)
                    else doc.get("title", "")
                )
                year_raw = (
                    doc.get("year_cluster", [""])[0]
                    if isinstance(doc.get("year_cluster"), list)
                    else doc.get("year_cluster", "")
                )
                papers.append(
                    Paper(
                        title=title,
                        doi=doc.get("doi", ""),
                        year=int(year_raw) if year_raw and str(year_raw).isdigit() else None,
                        authors=[Author(name=n) for n in (doc.get("au") or [])[:10]],
                        journal=(
                            doc.get("journal_title", [""])[0]
                            if isinstance(doc.get("journal_title"), list)
                            else doc.get("journal_title", "")
                        ),
                        source_type="peer_reviewed",
                        source_api="scielo_direct",
                        is_open_access=True,
                    )
                )
            return papers
        except Exception:
            return []
