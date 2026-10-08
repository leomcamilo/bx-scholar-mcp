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


# Enough to verify a co-author well past the 10th position without carrying the
# thousands of names of large physics collaborations.
MAX_AUTHORS = 100


class Author(BaseModel):
    """Canonical author representation.

    ``name`` is the display form. When the source separates the parts (Crossref
    family/given, Europe PMC lastName/firstName, "Last, First" catalogs), they
    are kept in ``family``/``given`` with ``structure_source="source"``, so
    citation matching never has to guess which word is the surname.
    Organizations keep their name in ``literal`` with ``kind="organization"``.
    """

    name: str
    family: str = ""
    given: str = ""
    literal: str = ""
    kind: Literal["person", "organization"] = "person"
    structure_source: Literal["source", "inferred", "none"] = "none"
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
    # Crossref keeps the subtitle apart; the full title is "title: subtitle".
    subtitle: str = ""
    doi: str = ""
    year: int | None = None
    authors: list[Author] = Field(default_factory=list)
    # The source holds more authors than it returned (OpenAlex stops at 100).
    authors_truncated: bool = False
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
    pmid: str = ""
    pmcid: str = ""
    # Source-specific ids without a dedicated field (CORE, Lens, KCI, CiNii, handle...)
    external_ids: dict[str, str] = Field(default_factory=dict)

    # Biomedical subject headings (MeSH descriptor names), from PubMed via OpenAlex
    # or Europe PMC
    mesh: list[str] = Field(default_factory=list)
    language: str = ""  # ISO 639-1 when the source provides it
    landing_url: str = ""  # record page for sources without DOI (theses, repositories)

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
