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
# "organization") on long words.
TOKEN_SIMILARITY = 85
# Numbers and short terms (IL-6, AI, COVID-19, BRCA1) carry the identity of a
# title: every one in the fragment must appear, exactly, in the record title,
# whatever the coverage of the other words. Coverage alone let "IL-6" pass for
# "IL-8" once the rest of a long title matched.
DISCRIMINANT_MAX_LEN = 3
# Share of the fragment's content words that must appear in the record title.
MIN_TITLE_COVERAGE = 0.8
# A one-word fragment can't be checked by coverage; it must match the whole title
# (or the main title before a subtitle).
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
_UNSPACED = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_SUBTITLE = re.compile("\\s*[:?!]\\s+|\\s+[-\u2013\u2014]\\s+")  # colon, or a dash between spaces


def _normalize(text: str) -> str:
    """Lowercase, strip accents, keep letters of any script and digits.

    Letters and digits are split apart and every other character is a separator,
    so "COVID-19", "COVID\u201319 (en dash)", "COVID 19" and "COVID19" all become "covid 19",
    and "IL-6" / "IL6" become "il 6". The number then stands as its own token.
    """
    text = unicodedata.normalize("NFKD", text.lower())
    text = unicodedata.normalize("NFC", "".join(c for c in text if not unicodedata.combining(c)))
    text = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", text)
    return re.sub(r"[\W_]+", " ", text).strip()


def _content_tokens(text: str) -> list[str]:
    return [
        t for t in _normalize(text).split() if t not in _STOPWORDS and (len(t) > 1 or t.isdigit())
    ]


def _discriminants(normalized: str) -> list[str]:
    """Numbers and short Latin terms that must match exactly ("il", "6", "ai")."""
    return [
        t
        for t in re.findall(r"[a-z]+|\d+", normalized)
        if t.isdigit() or (len(t) <= DISCRIMINANT_MAX_LEN and len(t) > 1 and t not in _STOPWORDS)
    ]


def _has_term(term: str, normalized: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalized) is not None


def _token_in(token: str, pool: list[str]) -> bool:
    if len(token) <= DISCRIMINANT_MAX_LEN or token.isdigit():
        return token in pool
    return any(fuzz.ratio(token, p) >= TOKEN_SIMILARITY for p in pool)


def _title_match(fragment: str, title: str) -> tuple[float, bool]:
    """(coverage, ok) of a title fragment against a record title."""
    frag, full = _normalize(fragment), _normalize(title)
    if not frag or not full:  # e.g. only punctuation, or nothing left
        return 0.0, False
    if not all(_has_term(t, full) for t in _discriminants(frag)):
        return 0.0, False

    if _UNSPACED.search(fragment):
        f, t = frag.replace(" ", ""), full.replace(" ", "")
        if f == t:
            return 1.0, True
        if len(f) < UNSPACED_MIN_CHARS:
            return 0.0, False
        coverage = fuzz.partial_ratio(f, t) / 100
        return coverage, coverage * 100 >= SINGLE_WORD_TITLE_RATIO

    frag_tokens = _content_tokens(fragment)
    if len(frag_tokens) >= 2:
        title_tokens = _content_tokens(title)
        coverage = sum(_token_in(t, title_tokens) for t in frag_tokens) / len(frag_tokens)
        return coverage, coverage >= MIN_TITLE_COVERAGE

    # One content word (or none, e.g. "What are the limits"): compare the whole
    # fragment, stopwords included, with the title or its main part.
    main = _normalize(_SUBTITLE.split(title, maxsplit=1)[0])
    coverage = max(fuzz.ratio(frag, full), fuzz.ratio(frag, main)) / 100
    return coverage, coverage * 100 >= SINGLE_WORD_TITLE_RATIO


@dataclass(frozen=True)
class _CitedName:
    words: list[str]  # full name parts, surname particles dropped
    initials: list[str]  # single letters ("J. D." or "JD" in "Smith JD")


def _cited_name(author: str) -> _CitedName:
    """Parse the first cited author.

    "Silva, L. C.; Souza" -> words ["silva"], initials ["l", "c"]
    "Smith JD"            -> words ["smith"], initials ["j", "d"]
    "World Health Organization (WHO)" -> words ["world", "health", "organization"]
    """
    first = re.split(r";|&|\bet al\b|\band\b|\s+e\s+|\s+y\s+", author, maxsplit=1, flags=re.I)[0]
    first = re.sub(r"\([^)]*\)", " ", first)  # "(WHO)" and similar asides
    raw = [w for w in re.split(r"[\s,.]+", first) if w]
    has_lower = any(not w.isupper() for w in raw)
    if len(raw) == 1 and raw[0].isupper() and raw[0].isalpha() and 2 <= len(raw[0]) <= 6:
        return _CitedName([], list(_normalize(raw[0])))  # a bare acronym like "WHO"
    words: list[str] = []
    initials: list[str] = []
    for w in raw:
        norm = _normalize(w)
        if not norm or norm in _AUTHOR_NOISE:
            continue
        if len(norm) == 1:
            initials.append(norm)
        elif has_lower and w.isupper() and len(w) <= 3:  # "JD" in "Smith JD"
            initials.extend(norm)
        elif norm not in _SURNAME_PARTICLES:
            words.extend(norm.split())
    return _CitedName(words, initials)


def _name_part_in(word: str, names: list[str]) -> bool:
    """A cited name part matches a record part, or a record initial ("Camilo" ~ "C.")."""
    for n in names:
        if n == word or (len(n) == 1 and n == word[0]):
            return True
        if len(n) > 3 and len(word) > 3 and fuzz.ratio(word, n) >= TOKEN_SIMILARITY:
            return True
    return False


def _author_matches(cited: _CitedName, paper: Paper) -> bool | None:
    """True if one record author is compatible with the whole cited name.

    Every cited name part must match a part (or initial) of the same record
    author, and at least one part must match in full, so a shared given name
    ("John") is never enough. Word order is free: "Wang Wei" and "W. Wang" agree.
    None when there is nothing to check on either side.
    """
    authors = [_normalize(a.name).split() for a in paper.authors if a.name.strip()]
    if not authors or not (cited.words or cited.initials):
        return None
    for names in authors:
        if not cited.words:  # only an acronym like "WHO": match it against the initials
            if [n[0] for n in names if n not in _SURNAME_PARTICLES] == cited.initials:
                return True
            continue
        full_hit = any(
            w in names
            or any(
                len(n) > 3 and len(w) > 3 and fuzz.ratio(w, n) >= TOKEN_SIMILARITY for n in names
            )
            for w in cited.words
        )
        if (
            full_hit
            and all(_name_part_in(w, names) for w in cited.words)
            and all(any(n[0] == i for n in names) for i in cited.initials)
        ):
            return True
    return False


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
    author_ok = _author_matches(_cited_name(author), paper)
    year_delta = abs(paper.year - year) if year and paper.year else None
    return CitationMatch(paper, coverage, title_ok, author_ok, year_delta)


def best_match(
    papers: list[Paper], author: str, year: int | None, title_fragment: str
) -> CitationMatch | None:
    scored = [score_candidate(p, author, year, title_fragment) for p in papers]
    return max(scored, key=CitationMatch._rank, default=None)
