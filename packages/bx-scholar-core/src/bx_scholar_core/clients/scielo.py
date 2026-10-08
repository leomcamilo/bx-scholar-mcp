"""SciELO client — Brazilian/LATAM Open Access papers, via OpenAlex.

OpenAlex has no usable "hosted on SciELO" filter (``host_venue`` was removed
and returns 400; SciELO's own repository sources hold almost no works), so
SciELO Brasil is selected by its DOI prefix, 10.1590. search.scielo.org
blocks API clients (403), hence no direct fallback. Other national
collections (Chile 10.4067, etc.) are not covered yet.
"""

from __future__ import annotations

from bx_scholar_core.clients.base import AsyncHTTPClient
from bx_scholar_core.clients.openalex import _parse_work
from bx_scholar_core.models.paper import Paper

OPENALEX_WORKS = "https://api.openalex.org/works"
SCIELO_BRASIL_DOI_PREFIX = "10.1590"


class SciELOClient(AsyncHTTPClient):
    """Client for SciELO via OpenAlex.

    Rate limit: 5 req/s.
    Open-access status is taken from each record, not assumed.
    """

    base_url = ""
    rate_limit = 5.0
    max_rate_period = 1.0

    def __init__(
        self, polite_email: str, user_agent: str = "", api_key: str = "", **kwargs
    ) -> None:
        ua = user_agent or f"BX-Scholar/0.1.0 (mailto:{polite_email})"
        super().__init__(user_agent=ua, **kwargs)
        self._polite_email = polite_email
        self._api_key = api_key

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        max_results: int = 20,
    ) -> list[Paper]:
        """Search SciELO Brasil papers. HTTP errors propagate to the caller."""
        oa_filter = f"doi_starts_with:{SCIELO_BRASIL_DOI_PREFIX}"
        if year_from:
            oa_filter += f",publication_year:>{year_from - 1}"
        if year_to:
            oa_filter += f",publication_year:<{year_to + 1}"

        resp = await self.get(
            OPENALEX_WORKS,
            params={
                "search": query,
                "filter": oa_filter,
                "sort": "relevance_score:desc",
                "per_page": min(max_results, 50),
                "mailto": self._polite_email,
                **({"api_key": self._api_key} if self._api_key else {}),
            },
            cache_policy=("search_results", 3600),
        )
        papers: list[Paper] = []
        for work in resp.json().get("results", []):
            p = _parse_work(work)
            p.source_api = "scielo_via_openalex"
            # Access status comes from the record (_parse_work), never assumed:
            # telling the user a paper is open asserts they can read it.
            oa_url = (work.get("open_access") or {}).get("oa_url")
            if oa_url:
                p.pdf_url = oa_url
            papers.append(p)
        return papers
