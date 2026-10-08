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

# Per-token fuzzy threshold: absorbs plurals and OCR typos ("organisation" /
# "organization") on long words. Short tokens and tokens with digits must match
# exactly: AI/AR, IL-6/IL-8 or COVID-19/COVID-20 are different things.
TOKEN_SIMILARITY = 85
EXACT_MAX_LEN = 3
# Share of the fragment's content words that must appear in the record title.
MIN_TITLE_COVERAGE = 0.8
# A one-word fragment can't be checked by coverage; it must match the whole title.
SINGLE_WORD_TITLE_RATIO = 90
# Scripts written without spaces (CJK) are compared as character strings.
UNSPACED_MIN_CHARS = 3
MAX_YEAR_DELTA = 1

_STOPWORDS = frozenset(
    ["the", "and", "for", "with", "from", "into", "onto", "over", "under", "about", "between", "among", "of", "in", "on", "at", "to", "by", "an", "its", "their", "this", "that", "these", "those", "how", "what", "why", "when", "where", "which", "who", "is", "are", "o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "sobre", "entre", "e", "ou", "que", "como", "el", "la", "los", "las", "del", "y", "en", "con", "se"]
)  # fmt: skip
_AUTHOR_NOISE = frozenset({"et", "al", "jr", "sr", "eds", "ed", "org", "orgs"})
_SURNAME_PARTICLES = frozenset(
    {"da", "de", "do", "das", "dos", "di", "du", "del", "der", "van", "von", "la", "le"}
)
_UNSPACED = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def _normalize(text: str) -> str:
    """Lowercase, strip accents, keep letters of any script and digits.

    A hyphen between a letter and a digit is dropped so "IL-6" and "IL6" agree;
    other hyphens split words ("self-driving" -> "self driving").
    """
    text = unicodedata.normalize("NFKD", text.lower())
    text = unicodedata.normalize("NFC", "".join(c for c in text if not unicodedata.combining(c)))
    text = re.sub(r"(?<=[^\W\d_])-(?=\d)|(?<=\d)-(?=[^\W\d_])", "", text)
    return re.sub(r"[\W_]+", " ", text).strip()


def _content_tokens(text: str) -> list[str]:
    return [
        t for t in _normalize(text).split() if t not in _STOPWORDS and (len(t) > 1 or t.isdigit())
    ]


def _token_in(token: str, pool: list[str]) -> bool:
    if len(token) <= EXACT_MAX_LEN or any(c.isdigit() for c in token):
        return token in pool
    return any(fuzz.ratio(token, p) >= TOKEN_SIMILARITY for p in pool)


def _cited_surname(author: str) -> list[str]:
    """Surname tokens of the first cited author.

    "Silva, L. C.; Souza" -> ["silva"]; "John Smith" -> ["smith"];
    "Smith J" -> ["smith"]; "da Silva, L." -> ["silva"]; "Li" -> ["li"].
    """
    first = re.split(r";|&|\bet al\b|\band\b|\s+e\s+|\s+y\s+", author, maxsplit=1, flags=re.I)[0]
    if "," in first:
        part = first.split(",")[0]
    else:
        words = [w for w in _normalize(first).split() if len(w) > 1 and w not in _AUTHOR_NOISE]
        part = words[-1] if words else ""
    return [w for w in _normalize(part).split() if w not in _SURNAME_PARTICLES and len(w) > 1]


def _author_matches(surname: list[str], paper: Paper) -> bool | None:
    """True if one record author carries every surname token; None if not checkable."""
    authors = [_normalize(a.name).split() for a in paper.authors if a.name.strip()]
    if not surname or not authors:
        return None
    return any(all(_token_in(s, names) for s in surname) for names in authors)


def _title_match(fragment: str, title: str) -> tuple[float, bool]:
    """(coverage, ok) of a title fragment against a record title."""
    if _UNSPACED.search(fragment):
        frag, full = _normalize(fragment).replace(" ", ""), _normalize(title).replace(" ", "")
        if len(frag) < UNSPACED_MIN_CHARS or not full:
            return 0.0, False
        coverage = fuzz.partial_ratio(frag, full) / 100
        return coverage, coverage * 100 >= SINGLE_WORD_TITLE_RATIO

    frag_tokens = _content_tokens(fragment)
    if len(frag_tokens) >= 2:
        title_tokens = _content_tokens(title)
        coverage = sum(_token_in(t, title_tokens) for t in frag_tokens) / len(frag_tokens)
        return coverage, coverage >= MIN_TITLE_COVERAGE

    frag, full = _normalize(fragment), _normalize(title)
    if not frag or not full:  # e.g. only stopwords or punctuation left
        return 0.0, False
    coverage = fuzz.ratio(frag, full) / 100
    return coverage, coverage * 100 >= SINGLE_WORD_TITLE_RATIO


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
    coverage, title_ok = _title_match(title_fragment, paper.title)
    author_ok = _author_matches(_cited_surname(author), paper)
    year_delta = abs(paper.year - year) if year and paper.year else None
    return CitationMatch(paper, coverage, title_ok, author_ok, year_delta)


def best_match(
    papers: list[Paper], author: str, year: int | None, title_fragment: str
) -> CitationMatch | None:
    scored = [score_candidate(p, author, year, title_fragment) for p in papers]
    return max(scored, key=CitationMatch._rank, default=None)
