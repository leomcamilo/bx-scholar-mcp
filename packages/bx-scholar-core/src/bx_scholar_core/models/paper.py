"""Canonical models for papers, authors, and venues."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

SourceType = Literal[
    "peer_reviewed",
    "grey_literature",
    "preprint",
    "book",
    "book_chapter",
    "conference",
    "thesis",
    "report",
    "web",
    "unknown",
]


class Author(BaseModel):
    """Canonical author representation."""

    name: str
    openalex_id: str = ""
    orcid: str = ""
    h_index: int | None = None
    works_count: int | None = None
    cited_by_count: int | None = None


class Venue(BaseModel):
    """Canonical venue (journal/conference) representation."""

    name: str
    issn_l: str = ""
    issns: list[str] = Field(default_factory=list)
    publisher: str = ""
    type: str = ""  # journal, conference, repository, etc.
    is_open_access: bool = False


class Paper(BaseModel):
    """Canonical paper representation, independent of source API."""

    title: str
    doi: str = ""
    year: int | None = None
    authors: list[Author] = Field(default_factory=list)
    abstract: str = ""
    cited_by_count: int = 0
    source_type: SourceType = "unknown"

    # Venue info
    journal: str = ""
    issn: str = ""
    venue: Venue | None = None

    # IDs from various sources
    openalex_id: str = ""
    s2_id: str = ""
    arxiv_id: str = ""

    # Open Access
    is_open_access: bool = False
    pdf_url: str = ""

    # Semantic Scholar extras
    tldr: str = ""
    influential_citation_count: int = 0

    # Source tracking
    source_api: str = ""  # which API returned this result

    # References (OpenAlex IDs or DOIs)
    references: list[str] = Field(default_factory=list)

    @field_validator("doi", mode="before")
    @classmethod
    def normalize_doi(cls, v: str) -> str:
        if not v:
            return ""
        v = v.strip()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if v.lower().startswith(prefix.lower()):
                v = v[len(prefix) :]
        return v

    @field_validator("issn", mode="before")
    @classmethod
    def normalize_issn(cls, v: str) -> str:
        if not v:
            return ""
        v = v.strip().upper()
        # Add hyphen if missing: 12345678 -> 1234-5678
        if len(v) == 8 and "-" not in v:
            v = f"{v[:4]}-{v[4:]}"
        return v
