"""Decide whether a search hit really is the cited work.

Search APIs always return *something*; the anti-hallucination guarantee lives
here. A hit counts as the citation only when the title fragment, the cited
author and the year all agree with it. Missing metadata on the record (no
authors, no year) is tolerated but caps confidence at "medium"; a positive
mismatch rejects the hit.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from bx_scholar_core.models.paper import Paper

# Per-token fuzzy threshold: absorbs plurals, hyphenation and OCR typos
# ("organisation"/"organization") without letting distinct words collide.
TOKEN_SIMILARITY = 85
# Share of the fragment's content words that must appear in the record title.
MIN_TITLE_COVERAGE = 0.8
# A one-word fragment can't be checked by coverage; it must match the whole title.
SINGLE_WORD_TITLE_RATIO = 90
MAX_YEAR_DELTA = 1

_STOPWORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "onto",
        "over",
        "under",
        "about",
        "between",
        "among",
        "of",
        "in",
        "on",
        "at",
        "to",
        "by",
        "an",
        "its",
        "their",
        "this",
        "that",
        "these",
        "those",
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "who",
        "o",
        "a",
        "os",
        "as",
        "um",
        "uma",
        "uns",
        "umas",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "para",
        "por",
        "com",
        "sem",
        "sobre",
        "entre",
        "e",
        "ou",
        "que",
        "como",
        "el",
        "la",
        "los",
        "las",
        "del",
        "y",
        "en",
        "por",
        "para",
        "con",
    ]
)
_AUTHOR_NOISE = frozenset({"et", "al", "and", "e", "y", "jr", "sr", "eds", "ed", "org", "orgs"})


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _content_tokens(text: str) -> list[str]:
    return [t for t in _normalize(text).split() if t not in _STOPWORDS and len(t) > 2]


def _first_author_tokens(author: str) -> list[str]:
    """Surname candidates of the first cited author ("Silva, L.; Souza" -> ["silva"])."""
    first = re.split(r";|&|\bet al\b|\band\b|\s+e\s+", author, maxsplit=1, flags=re.I)[0]
    return [t for t in _normalize(first).split() if len(t) > 2 and t not in _AUTHOR_NOISE]


def _fuzzy_in(token: str, pool: list[str]) -> bool:
    return any(fuzz.ratio(token, p) >= TOKEN_SIMILARITY for p in pool)


@dataclass(frozen=True)
class CitationMatch:
    paper: Paper
    title_coverage: float
    title_ok: bool
    author_ok: bool | None  # None: no author given or record has no authors
    year_delta: int | None  # None: no year given or record has no year

    @property
    def year_ok(self) -> bool | None:
        return None if self.year_delta is None else self.year_delta <= MAX_YEAR_DELTA

    @property
    def verified(self) -> bool:
        return self.title_ok and self.author_ok is not False and self.year_ok is not False

    @property
    def confidence(self) -> Literal["high", "medium", "low", "none"]:
        if self.verified:
            fully_checked = self.author_ok is True and self.year_delta == 0
            return "high" if fully_checked and self.title_coverage >= 0.9 else "medium"
        return "low" if self.title_ok else "none"

    def reasons(self) -> list[str]:
        out = []
        if not self.title_ok:
            out.append(f"title fragment matches only {self.title_coverage:.0%} of content words")
        if self.author_ok is False:
            out.append("cited author not among the record's authors")
        elif self.author_ok is None:
            out.append("author not checked (missing on query or record)")
        if self.year_ok is False:
            out.append(f"year differs by {self.year_delta}")
        elif self.year_delta is None:
            out.append("year not checked (missing on query or record)")
        return out

    def checks(self) -> dict[str, object]:
        return {
            "title_coverage": round(self.title_coverage, 2),
            "author_match": self.author_ok,
            "year_delta": self.year_delta,
        }

    def _rank(self) -> tuple[bool, bool, float, bool, int]:
        return (
            self.verified,
            self.title_ok,
            self.title_coverage,
            self.author_ok is True,
            -(self.year_delta if self.year_delta is not None else MAX_YEAR_DELTA),
        )


def score_candidate(
    paper: Paper, author: str, year: int | None, title_fragment: str
) -> CitationMatch:
    frag_tokens = _content_tokens(title_fragment)
    title_tokens = _content_tokens(paper.title)
    if len(frag_tokens) >= 2:
        hits = sum(_fuzzy_in(t, title_tokens) for t in frag_tokens)
        coverage = hits / len(frag_tokens)
        title_ok = coverage >= MIN_TITLE_COVERAGE
    else:
        coverage = fuzz.ratio(_normalize(title_fragment), _normalize(paper.title)) / 100
        title_ok = coverage * 100 >= SINGLE_WORD_TITLE_RATIO

    query_surnames = _first_author_tokens(author)
    record_names = [t for a in paper.authors for t in _normalize(a.name).split()]
    author_ok = (
        any(_fuzzy_in(s, record_names) for s in query_surnames)
        if query_surnames and record_names
        else None
    )

    year_delta = abs(paper.year - year) if year and paper.year else None
    return CitationMatch(paper, coverage, title_ok, author_ok, year_delta)


def best_match(
    papers: list[Paper], author: str, year: int | None, title_fragment: str
) -> CitationMatch | None:
    scored = [score_candidate(p, author, year, title_fragment) for p in papers]
    return max(scored, key=CitationMatch._rank, default=None)
