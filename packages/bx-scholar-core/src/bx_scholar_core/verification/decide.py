"""Decide whether retrieved candidates confirm a citation.

Each candidate gets a state per field. A conflict in a field the user gave
vetoes that candidate; a field missing on the record makes it insufficient,
never confirmed. Exactly one verifiable work confirms; two or more distinct
works make the answer ambiguous. Similarity only ranks what is shown as the
closest match; it never confirms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz

from bx_scholar_core.models.paper import Author, Paper
from bx_scholar_core.verification.names import AuthorState, compare_authors
from bx_scholar_core.verification.titles import TitleEvidence, TitleMode, compare_title, fold

MAX_YEAR_DELTA = 1

Status = Literal["verified", "conflict", "insufficient", "ambiguous"]
YearState = Literal["exact", "off_by_one", "conflict", "unknown", "not_given"]


@dataclass(frozen=True)
class Query:
    author: str
    year: int | None
    title: str
    title_mode: TitleMode = "auto"


@dataclass
class CandidateEvidence:
    paper: Paper
    sources: list[str]
    title: TitleEvidence
    author: AuthorState
    author_detail: str
    year: YearState
    year_delta: int | None
    query_title: str = ""

    @property
    def author_ok(self) -> bool:
        return self.author in ("exact", "compatible") or (
            self.author == "unknown" and self.author_detail == "no author given"
        )

    @property
    def year_ok(self) -> bool:
        return self.year in ("exact", "off_by_one", "not_given")

    @property
    def verifiable(self) -> bool:
        return self.title.confirms and self.author_ok and self.year_ok

    @property
    def has_conflict(self) -> bool:
        return "conflict" in (self.title.state, self.author, self.year)

    @property
    def confidence(self) -> Literal["high", "medium", "low", "none"]:
        if self.verifiable:
            exact = (
                self.author == "exact"
                and self.year == "exact"
                and self.title.state in ("full", "main", "fragment")
            )
            return "high" if exact else "medium"
        return "low" if self.title.confirms else "none"

    def reasons(self) -> list[str]:
        out = []
        if not self.title.confirms:
            out.append(f"title: {self.title.detail}")
        if self.author == "conflict":
            out.append(f"author: {self.author_detail}")
        elif self.author == "unknown" and self.author_detail != "no author given":
            out.append(f"author not checked: {self.author_detail}")
        if self.year == "conflict":
            out.append(f"year differs by {self.year_delta}")
        elif self.year == "unknown":
            out.append("year not checked: record has no publication year")
        return out

    def warnings(self) -> list[str]:
        out = []
        if self.year == "off_by_one":
            out.append("year differs by 1 (online-first and print years often differ)")
        if self.author == "compatible":
            out.append(f"author: {self.author_detail}")
        if self.author == "unknown":
            out.append("author not given: authorship was not checked")
        if self.year == "not_given":
            out.append("year not given: publication year was not checked")
        return out

    def checks(self) -> dict[str, object]:
        return {
            "title_match": self.title.state,
            "title_detail": self.title.detail,
            "author_match": self.author,
            "author_detail": self.author_detail,
            "year_match": self.year,
            "year_delta": self.year_delta,
        }

    def _rank(self) -> tuple[bool, bool, bool, bool, float]:
        """Closest-match ordering only; never used to confirm."""
        return (
            self.verifiable,
            self.title.confirms,
            self.author_ok,
            self.year_ok,
            fuzz.token_sort_ratio(fold(self.paper.title), fold(self.query_title)),
        )


def _unpadded(doi: str) -> str:
    prefix, _, suffix = doi.partition("/")
    return f"{prefix}/" + re.sub(r"(?<=\.)0+(?=[0-9]+(?:\.|$))", "", suffix)


def _name_key(a: Author) -> str:
    return " ".join(fold(a.name or f"{a.given} {a.family}").replace(".", " ").split())


def _same_record(a: Paper, b: Paper) -> bool:
    return (
        fold(f"{a.title} {a.subtitle}") == fold(f"{b.title} {b.subtitle}")
        and a.year == b.year
        and bool(a.authors)
        # whole names, not surnames: John Smith and James Smith are not one record
        and [_name_key(x) for x in a.authors] == [_name_key(x) for x in b.authors]
    )


def _identity(p: Paper) -> str:
    if p.doi:
        return f"doi:{p.doi.strip().lower()}"
    if p.openalex_id:
        return f"oa:{p.openalex_id}"
    return f"t:{fold(p.title)}|{p.year}"


def merge_candidates(found: list[tuple[str, Paper]]) -> list[tuple[list[str], Paper]]:
    """One entry per work. Records of the same DOI from different sources are
    combined: a field missing in one is taken from the other (the structured
    Crossref record first). Different works are never combined."""
    groups: dict[str, list[tuple[str, Paper]]] = {}
    for source, paper in found:
        groups.setdefault(_identity(paper), []).append((source, paper))
    _join_unpadded(groups)
    merged = []
    for items in groups.values():
        items.sort(key=lambda sp: sp[0] != "crossref")
        sources = [s for s, _ in items]
        base = items[0][1].model_copy(deep=True)
        for _, other in items[1:]:
            if not base.authors and other.authors:
                base.authors = other.authors
                base.authors_truncated = other.authors_truncated
            if base.year is None and other.year is not None:
                base.year = other.year
            if not base.title and other.title:
                base.title = other.title
            if not base.openalex_id and other.openalex_id:
                base.openalex_id = other.openalex_id
        merged.append((sorted(set(sources)), base))
    return merged


def _join_unpadded(groups: dict[str, list[tuple[str, Paper]]]) -> None:
    """DOIs are opaque, so two DOIs are two works. One exception, seen in
    OpenAlex: it drops leading zeros Crossref keeps ("...26.1.045" becomes
    "...26.1.45"). A Crossref-only and an OpenAlex-only group are joined when
    their DOIs differ only that way AND their records are the same (title,
    year, author names). Nothing is filled in from one into the other."""
    keys = [k for k in groups if k.startswith("doi:")]
    for k in keys:
        if k not in groups or {s for s, _ in groups[k]} != {"crossref"}:
            continue
        for other in keys:
            if (
                other != k
                and other in groups
                and {s for s, _ in groups[other]} == {"openalex"}
                and _unpadded(other) == _unpadded(k)
                and all(_same_record(p, q) for _, p in groups[k] for _, q in groups[other])
            ):
                groups[k].extend(groups.pop(other))


def assess_candidate(query: Query, paper: Paper, sources: list[str]) -> CandidateEvidence:
    title = compare_title(query.title, paper.title, paper.subtitle, query.title_mode)
    author, author_detail = compare_authors(query.author, paper.authors, paper.authors_truncated)
    if query.year is None:
        year: YearState = "not_given"
        delta = None
    elif paper.year is None:
        year, delta = "unknown", None
    else:
        delta = abs(paper.year - query.year)
        year = "exact" if delta == 0 else "off_by_one" if delta <= MAX_YEAR_DELTA else "conflict"
    return CandidateEvidence(paper, sources, title, author, author_detail, year, delta, query.title)


@dataclass
class Decision:
    status: Status
    best: CandidateEvidence | None
    verifiable: list[CandidateEvidence] = field(default_factory=list)
    candidates: int = 0

    @property
    def next_action(self) -> str:
        if self.status == "verified":
            return ""
        if self.status == "ambiguous":
            return (
                "Several works match: give the full title, the year or the DOI to tell them apart."
            )
        if self.best is None:
            return (
                "No candidate found. Check the title spelling, give the full title or the DOI; "
                "absence from Crossref/OpenAlex does not by itself prove the work does not exist."
            )
        reasons = " ".join(self.best.reasons())
        if (
            self.best.title.confirms
            and self.best.author == "conflict"
            and self.best.year == "conflict"
        ):
            return (
                "Only a different work with the same title was found (other authors and "
                "year). Confirm with the DOI, ISBN or the publisher's record."
            )
        if "author" in reasons and "author not checked" not in reasons:
            return "The closest work has different authors: check the cited author."
        if "year differs" in reasons:
            return "The closest work has a different year: check the cited year."
        if "title" in reasons:
            return (
                "Give the full title (or a literal passage of at least 4 content words) to confirm."
            )
        return "The record lacks author or year: confirm with the DOI or another source."


def decide(query: Query, found: list[tuple[str, Paper]]) -> Decision:
    evidence = [assess_candidate(query, p, s) for s, p in merge_candidates(found)]
    if not evidence:
        return Decision("insufficient", None, [], 0)
    ok = [e for e in evidence if e.verifiable]
    if len(ok) == 1:
        return Decision("verified", ok[0], ok, len(evidence))
    if len(ok) > 1:
        return Decision("ambiguous", max(ok, key=CandidateEvidence._rank), ok, len(evidence))
    best = max(evidence, key=CandidateEvidence._rank)
    # A conflict is only demonstrated against a work the title identifies (or
    # against the full title the user vouched for).
    identified_conflict = [
        e
        for e in evidence
        # The title identifies the work and the author or the year corroborates
        # it. Title alone, with both author and year different, is more likely a
        # different work with the same title (a review, a homonymous book) than
        # a wrong citation of this one.
        if (e.title.confirms and e.has_conflict and (e.author_ok or e.year_ok))
        # full title vouched for, same authors and year, different title: a near
        # miss (typo, changed word). An unrelated search hit is not a conflict.
        or (e.title.state == "conflict" and e.author in ("exact", "compatible") and e.year_ok)
    ]
    if identified_conflict:
        best = max(identified_conflict, key=CandidateEvidence._rank)
        return Decision("conflict", best, [], len(evidence))
    return Decision("insufficient", best, [], len(evidence))
