"""ArXiv API client — preprints (grey literature)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from bx_scholar_core.clients.base import AsyncHTTPClient
from bx_scholar_core.models.paper import Author, Paper

ARXIV_BASE = "https://export.arxiv.org/api/query"
_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArXivClient(AsyncHTTPClient):
    """Client for the ArXiv API.

    Rate limit: 1 request per 3 seconds (ArXiv policy).
    All results are grey literature (not peer-reviewed).
    """

    base_url = ""  # we use full URL
    rate_limit = 1.0
    max_rate_period = 3.0

    async def search(
        self,
        query: str,
        max_results: int = 20,
        sort_by: str = "relevance",
    ) -> list[Paper]:
        """Search ArXiv. All results are marked as grey_literature."""
        resp = await self.get(
            ARXIV_BASE,
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": min(max_results, 50),
                "sortBy": sort_by,
                "sortOrder": "descending",
            },
            cache_policy=("search_results", 3600),
        )

        root = ET.fromstring(resp.text)
        papers: list[Paper] = []

        for entry in root.findall("atom:entry", _NS):
            title_el = entry.find("atom:title", _NS)
            title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""

            summary_el = entry.find("atom:summary", _NS)
            abstract = (
                (summary_el.text or "").strip().replace("\n", " ")[:500]
                if summary_el is not None
                else ""
            )

            authors = []
            for a in entry.findall("atom:author", _NS):
                name_el = a.find("atom:name", _NS)
                if name_el is not None and name_el.text:
                    authors.append(Author(name=name_el.text))

            published_el = entry.find("atom:published", _NS)
            published = (published_el.text or "")[:10] if published_el is not None else ""

            id_el = entry.find("atom:id", _NS)
            arxiv_id = (id_el.text or "").split("/abs/")[-1] if id_el is not None else ""

            papers.append(
                Paper(
                    title=title,
                    doi="",
                    year=int(published[:4]) if len(published) >= 4 else None,
                    authors=authors[:10],
                    abstract=abstract,
                    arxiv_id=arxiv_id,
                    source_type="grey_literature",
                    source_api="arxiv",
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else "",
                )
            )

        return papers
