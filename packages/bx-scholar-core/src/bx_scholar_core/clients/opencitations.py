"""OpenCitations client — open DOI-to-DOI citations (Index v2) and metadata (Meta v1).

The Index returns every link for a DOI in one response (no paging, no
metadata); Meta resolves DOIs to bibliographic records in batches. An access
token is optional (free, by e-mail); without one the API still answers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError
from bx_scholar_core.models.paper import Author, Paper, SourceType

OPENCITATIONS_BASE = "https://api.opencitations.net"
META_BATCH = 25  # DOIs per Meta request; 25 resolve in ~2 s

_TYPES: dict[str, SourceType] = {
    "journal article": "peer_reviewed",
    "proceedings article": "conference",
    "book": "book",
    "book chapter": "book_chapter",
    "dissertation": "thesis",
    "report": "report",
    "posted content": "preprint",
}
_BRACKETS = re.compile(r"\s*\[[^\]]*\]")


def _path(doi: str) -> str:
    """A DOI inside the URL path. Real DOIs carry "?", "#", "%" or spaces-free
    oddities that would end the path or start a query; "/" and parentheses stay."""
    return quote(doi, safe="/()")


def _ids(field: str) -> dict[str, str]:
    """'omid:br/061 openalex:W29 doi:10.1/x' -> {'omid': 'br/061', 'openalex': 'W29', ...}"""
    out: dict[str, str] = {}
    for token in field.split():
        prefix, _, value = token.partition(":")
        if value and prefix not in out:
            out[prefix] = value
    return out


@dataclass(frozen=True)
class CitationLink:
    """One end of a citation link, as the Index reports it."""

    doi: str
    openalex_id: str
    created: str  # publication date of the citing work (YYYY, YYYY-MM or YYYY-MM-DD)
    author_self_citation: bool
    journal_self_citation: bool


def _person(name: str) -> str:
    last, sep, first = name.partition(",")
    return f"{first.strip()} {last.strip()}" if sep and first.strip() else name.strip()


def parse_meta(record: dict[str, Any]) -> Paper:
    ids = _ids(record.get("id", ""))
    authors = [
        Author(name=_person(_BRACKETS.sub("", a)))
        for a in (record.get("author") or "").split(";")
        if a.strip()
    ]
    year = re.match(r"\d{4}", record.get("pub_date") or "")
    venue = record.get("venue") or ""
    issn = re.search(r"issn:(\S+?)[\s\]]", venue)
    return Paper(
        title=record.get("title") or "",
        doi=ids.get("doi", ""),
        year=int(year.group()) if year else None,
        authors=authors[:10],
        journal=_BRACKETS.sub("", venue).strip(),
        issn=issn.group(1) if issn else "",
        openalex_id=ids.get("openalex", ""),
        pmid=ids.get("pmid", ""),
        source_type=_TYPES.get(record.get("type", ""), "unknown"),
        source_api="opencitations",
    )


class OpenCitationsClient(AsyncHTTPClient):
    """Client for the OpenCitations REST APIs.

    Rate limit: 3 req/s, a polite share of the public service.
    """

    base_url = OPENCITATIONS_BASE
    rate_limit = 3.0
    max_rate_period = 1.0
    timeout = 60.0  # highly cited DOIs return thousands of links

    def __init__(self, token: str = "", user_agent: str = "", **kwargs: Any) -> None:
        super().__init__(user_agent=user_agent or "BX-Scholar/0.1.0", **kwargs)
        self._token = token

    def _extra_headers(self) -> dict[str, str]:
        return {"authorization": self._token} if self._token else {}

    async def links(
        self, doi: str, direction: Literal["citing", "references"]
    ) -> list[CitationLink]:
        """Works citing ``doi`` ("citing") or cited by it ("references")."""
        endpoint, side = (
            ("citations", "citing") if direction == "citing" else ("references", "cited")
        )
        try:
            resp = await self.get(
                f"/index/v2/{endpoint}/doi:{_path(doi)}", cache_policy=("citations", 86400)
            )
        except NonRetryableHTTPError as exc:
            if exc.status_code == 404:  # DOI unknown to OpenCitations
                return []
            raise
        out = []
        for row in resp.json() or []:
            ids = _ids(row.get(side, ""))
            out.append(
                CitationLink(
                    doi=ids.get("doi", ""),
                    openalex_id=ids.get("openalex", ""),
                    created=row.get("creation", ""),
                    author_self_citation=row.get("author_sc") == "yes",
                    journal_self_citation=row.get("journal_sc") == "yes",
                )
            )
        return out

    async def metadata(self, dois: list[str]) -> list[Paper]:
        """Bibliographic records for DOIs, fetched in batches of META_BATCH."""
        papers: list[Paper] = []
        for i in range(0, len(dois), META_BATCH):
            batch = "__".join(f"doi:{_path(d)}" for d in dois[i : i + META_BATCH])
            resp = await self.get(
                f"/meta/v1/metadata/{batch}", cache_policy=("paper_metadata", 7 * 86400)
            )
            papers.extend(parse_meta(r) for r in resp.json() or [])
        return papers
