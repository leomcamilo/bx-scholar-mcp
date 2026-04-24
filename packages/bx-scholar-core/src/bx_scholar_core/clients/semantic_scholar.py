"""Semantic Scholar API client — TLDR, influential citations, citation context."""

from __future__ import annotations

from typing import Any

from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError
from bx_scholar_core.models.paper import Author, Paper

S2_BASE = "https://api.semanticscholar.org/graph/v1"


def _parse_s2_paper(paper: dict[str, Any]) -> Paper:
    """Parse a Semantic Scholar paper into a canonical Paper."""
    ext_ids = paper.get("externalIds") or {}
    journal = paper.get("journal") or {}
    tldr = paper.get("tldr")
    oa_pdf = paper.get("openAccessPdf")

    pub_types = paper.get("publicationTypes") or []
    if "JournalArticle" in pub_types:
        source_type = "peer_reviewed"
    elif "Conference" in pub_types:
        source_type = "conference"
    elif "Book" in pub_types:
        source_type = "book"
    else:
        source_type = "unknown"

    return Paper(
        title=paper.get("title") or "",
        doi=ext_ids.get("DOI", ""),
        year=paper.get("year"),
        authors=[Author(name=a.get("name", "")) for a in (paper.get("authors") or [])[:10]],
        cited_by_count=paper.get("citationCount", 0),
        influential_citation_count=paper.get("influentialCitationCount", 0),
        journal=journal.get("name", "") if isinstance(journal, dict) else str(journal),
        s2_id=paper.get("paperId", ""),
        arxiv_id=ext_ids.get("ArXiv", ""),
        tldr=tldr.get("text", "") if tldr else "",
        source_type=source_type,
        source_api="semantic_scholar",
        pdf_url=(oa_pdf.get("url", "") if oa_pdf else ""),
        venue=None,
    )


class SemanticScholarClient(AsyncHTTPClient):
    """Client for the Semantic Scholar API.

    Rate limit: 1 req/s without key, 5 req/s with key.
    """

    base_url = S2_BASE
    rate_limit = 1.0
    max_rate_period = 1.0
    max_retries = 3

    def __init__(self, api_key: str = "", user_agent: str = "") -> None:
        super().__init__(user_agent=user_agent or "BX-Scholar/0.1.0")
        self._api_key = api_key
        if api_key:
            self.rate_limit = 5.0
            self._limiter._rate_per_sec = 5.0

    def _extra_headers(self) -> dict[str, str]:
        if self._api_key:
            return {"x-api-key": self._api_key}
        return {}

    _SEARCH_FIELDS = (
        "title,authors,year,venue,externalIds,citationCount,"
        "influentialCitationCount,tldr,abstract,publicationTypes,"
        "journal,openAccessPdf"
    )

    async def search(
        self,
        query: str,
        year: str | None = None,
        fields_of_study: str | None = None,
        limit: int = 20,
    ) -> tuple[list[Paper], int]:
        """Search papers. Returns (papers, total_count)."""
        params: dict[str, Any] = {
            "query": query,
            "limit": min(limit, 100),
            "fields": self._SEARCH_FIELDS,
        }
        if year:
            params["year"] = year
        if fields_of_study:
            params["fieldsOfStudy"] = fields_of_study

        try:
            resp = await self.get("/paper/search", params=params)
            data = resp.json()
            papers = [_parse_s2_paper(p) for p in data.get("data", [])]
            return papers, data.get("total", 0)
        except (NonRetryableHTTPError, Exception):
            return [], 0

    async def get_influential_citations(
        self, doi_or_s2id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Get influential citations for a paper."""
        paper_id = (
            f"DOI:{doi_or_s2id}"
            if "/" in doi_or_s2id and not doi_or_s2id.startswith("DOI:")
            else doi_or_s2id
        )
        try:
            resp = await self.get(
                f"/paper/{paper_id}/citations",
                params={
                    "fields": (
                        "title,authors,year,venue,citationCount,"
                        "influentialCitationCount,isInfluential,"
                        "contexts,intents,externalIds"
                    ),
                    "limit": min(limit, 100),
                },
            )
            results = []
            for item in resp.json().get("data", []):
                citing = item.get("citingPaper", {})
                if not citing.get("title"):
                    continue
                ext_ids = citing.get("externalIds") or {}
                results.append(
                    {
                        "title": citing.get("title", ""),
                        "authors": [a.get("name", "") for a in (citing.get("authors") or [])[:5]],
                        "year": citing.get("year"),
                        "doi": ext_ids.get("DOI", ""),
                        "citation_count": citing.get("citationCount", 0),
                        "is_influential": item.get("isInfluential", False),
                        "intents": item.get("intents", []),
                        "contexts": (item.get("contexts") or [])[:3],
                    }
                )
            return results
        except (NonRetryableHTTPError, Exception):
            return []

    async def get_citation_context(self, citing_doi: str, cited_doi: str) -> dict[str, Any] | None:
        """Get citation context between two papers."""
        citing_id = (
            f"DOI:{citing_doi}"
            if "/" in citing_doi and not citing_doi.startswith("DOI:")
            else citing_doi
        )
        try:
            resp = await self.get(
                f"/paper/{citing_id}/references",
                params={
                    "fields": "title,authors,year,externalIds,contexts,intents,isInfluential",
                    "limit": 500,
                },
            )
            cited_doi_lower = cited_doi.lower().replace("doi:", "")
            for item in resp.json().get("data", []):
                ref = item.get("citedPaper", {})
                ref_doi = ((ref.get("externalIds") or {}).get("DOI") or "").lower()
                if ref_doi == cited_doi_lower or cited_doi_lower in ref_doi:
                    return {
                        "citing_paper": citing_doi,
                        "cited_paper": cited_doi,
                        "cited_title": ref.get("title", ""),
                        "is_influential": item.get("isInfluential", False),
                        "intents": item.get("intents", []),
                        "contexts": item.get("contexts", []),
                    }
            return None
        except (NonRetryableHTTPError, Exception):
            return None
