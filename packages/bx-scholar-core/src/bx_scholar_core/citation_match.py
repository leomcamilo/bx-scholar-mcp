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
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from rapidfuzz import fuzz

from bx_scholar_core.models.paper import Paper

# Per-token fuzzy threshold: absorbs plurals and OCR typos ("organisation" /
# "organization") on long words.
TOKEN_SIMILARITY = 85
# Terms that carry the identity of a title must all appear, exactly and as
# many times, in the record title, whatever the coverage of the other words:
# anything with a digit (IL6, H2O, C2H6O, BRCA1, 2019), short words (AI, ML)
# and roman numerals (Phase I / Phase II).
DISCRIMINANT_MAX_LEN = 3
_ROMAN = frozenset(
    [
        "i",
        "ii",
        "iii",
        "iv",
        "v",
        "vi",
        "vii",
        "viii",
        "ix",
        "x",
        "xi",
        "xii",
        "xiii",
        "xiv",
        "xv",
        "xx",
    ]
)
# Share of the fragment's content words that must appear in the record title.
MIN_TITLE_COVERAGE = 0.8
# A one-word fragment can't be checked by coverage; it must match the whole title
# (or the main title before a subtitle).
SINGLE_WORD_TITLE_RATIO = 90
# Scripts written without spaces (CJK) are compared as character strings.
UNSPACED_MIN_CHARS = 3
MAX_YEAR_DELTA = 1
# "COVID 19" and "Phase 2" are one term; "effects 6 hours" is not. A space
# joins letters to a following number only after a short, non-stopword run.
SPACE_JOIN_MAX_LEN = 5

_STOPWORDS = frozenset(
    ["the", "and", "for", "with", "from", "into", "onto", "over", "under", "about", "between", "among", "of", "in", "on", "at", "to", "by", "an", "its", "their", "this", "that", "these", "those", "how", "what", "why", "when", "where", "which", "who", "is", "are", "o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "sobre", "entre", "e", "ou", "que", "como", "el", "la", "los", "las", "del", "y", "en", "con", "se"]
)  # fmt: skip
_AUTHOR_NOISE = frozenset({"et", "al", "jr", "sr", "eds", "ed", "org", "orgs"})
_SURNAME_PARTICLES = frozenset(
    {"da", "de", "do", "das", "dos", "di", "du", "del", "der", "van", "von", "la", "le", "y"}
)
_UNSPACED = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"  # hyphen..em dash, minus
# Subtitle: after ":", "?" or "!", after an em dash, or after any dash between spaces.
# A hyphen inside a word ("self-driving") or an en dash in a range is not one.
_SUBTITLE = re.compile("\\s*[:?!]\\s+|\\s*\u2014\\s*|\\s+[-\u2013]\\s+")
_INSTITUTION = re.compile(
    r"organi[sz]ation|universi|ministr|institut|associa|societ|council|agenc|foundation|"
    r"department|commission|committee|consortium|\bcent(?:er|re)\b|\bbank\b|\boffice\b|"
    r"network|\bgroup\b|\bboard\b|academ|federation|programme|\bunion\b",
    re.I,
)


def _fold(text: str) -> str:
    """Lowercase and strip accents, keeping letters of any script."""
    text = unicodedata.normalize("NFKD", text.lower())
    return unicodedata.normalize("NFC", "".join(c for c in text if not unicodedata.combining(c)))


def _normalize(text: str) -> str:
    """Folded text as space-separated alphanumeric terms.

    Letters followed by a number become one term across any dash ("COVID-19",
    "COVID\u201319" -> "covid19") or across a space after a short word ("COVID 19",
    "Phase 2"). Terms like "h2o" or "c2h6o" are never split.
    """
    text = _fold(text)
    text = re.sub(rf"(?<=[^\W\d_])[-{_DASHES}](?=\d)", "", text)

    def join_space(m: re.Match[str]) -> str:
        word = m.group(1)
        if len(word) <= SPACE_JOIN_MAX_LEN and word not in _STOPWORDS:
            return word + m.group(2)
        return m.group(0)

    text = re.sub(r"\b([^\W\d_]+) (\d+)\b", join_space, text)
    return re.sub(r"[\W_]+", " ", text).strip()


def _content_tokens(text: str) -> list[str]:
    return [t for t in _normalize(text).split() if t not in _STOPWORDS]


def _is_discriminant(token: str) -> bool:
    """Numbers, roman numerals and short terms, single letters included
    ("Vitamin D" is not "Vitamin C")."""
    return (
        any(c.isdigit() for c in token)
        or token in _ROMAN
        or (len(token) <= DISCRIMINANT_MAX_LEN and token not in _STOPWORDS)
    )


def _discriminants_present(frag_tokens: list[str], title_tokens: list[str]) -> bool:
    """Every discriminant of the fragment, with its multiplicity, is in the title."""
    need = Counter(t for t in frag_tokens if _is_discriminant(t))
    have = Counter(title_tokens)
    return all(have[t] >= n for t, n in need.items())


def _token_in(token: str, pool: list[str]) -> bool:
    if _is_discriminant(token):
        return token in pool
    return any(fuzz.ratio(token, p) >= TOKEN_SIMILARITY for p in pool)


def _title_match(fragment: str, title: str) -> tuple[float, bool]:
    """(coverage, ok) of a title fragment against a record title."""
    frag, full = _normalize(fragment), _normalize(title)
    if not frag or not full:  # e.g. only punctuation, or nothing left
        return 0.0, False

    if _UNSPACED.search(fragment):
        # Latin/number runs inside CJK text ("城市交通COVID-19") are discriminants
        latin = [t for t in re.findall(r"[a-z0-9]+", frag) if _is_discriminant(t)]
        if not _discriminants_present(latin, re.findall(r"[a-z0-9]+", full)):
            return 0.0, False
        f, t = frag.replace(" ", ""), full.replace(" ", "")
        if f == t:
            return 1.0, True
        if len(f) < UNSPACED_MIN_CHARS:
            return 0.0, False
        coverage = fuzz.partial_ratio(f, t) / 100
        return coverage, coverage * 100 >= SINGLE_WORD_TITLE_RATIO

    frag_tokens = _content_tokens(fragment)
    title_tokens = _content_tokens(title)
    if not _discriminants_present(frag_tokens, title_tokens):
        return 0.0, False
    if len(frag_tokens) >= 2:
        coverage = sum(_token_in(t, title_tokens) for t in frag_tokens) / len(frag_tokens)
        return coverage, coverage >= MIN_TITLE_COVERAGE

    # One content word (or none, e.g. "What are the limits"): compare the whole
    # fragment, stopwords included, with the title or its main part.
    main = _normalize(_SUBTITLE.split(title, maxsplit=1)[0])
    coverage = max(fuzz.ratio(frag, full), fuzz.ratio(frag, main)) / 100
    return coverage, coverage * 100 >= SINGLE_WORD_TITLE_RATIO


# --- Authors -----------------------------------------------------------------
# One parser for the cited author and for every record author, so both sides
# read "Smith JD", "J.-D. Smith", "Kim, Min-Jun" or "Minjun Kim" the same way.
# The rule follows reference managers: the surname must match, and the first
# given name must be compatible (same name, or same initial).


@dataclass(frozen=True)
class _Part:
    text: str  # folded name ("minjun" for "Min-Jun"); "" for an initial
    initials: tuple[str, ...]  # ("m", "j") for "Min-Jun" or "M-J"


@dataclass(frozen=True)
class _Name:
    surname: tuple[_Part, ...]  # last part is the key; earlier ones may be abbreviated
    given: tuple[_Part, ...]


@dataclass(frozen=True)
class _Author:
    readings: tuple[_Name, ...]  # western, and eastern when the order is ambiguous
    institution: str  # folded name when the author is an organization, else ""
    acronyms: frozenset[str]


_INITIALS = re.compile(r"(?:[^\W\d_]\.?)(?:[-\s]?[^\W\d_]\.)*|[^\W\d_](?:-[^\W\d_])+")


def _part(token: str, grouped_initials_ok: bool) -> _Part | None:
    word = token.strip(".,")
    if not word:
        return None
    letters = [c for c in _fold(word) if c.isalpha()]
    looks_initials = bool(_INITIALS.fullmatch(word)) and (
        len(letters) == 1 or "." in word or "-" in word
    )
    if looks_initials or (grouped_initials_ok and word.isupper() and len(letters) <= 3):
        return _Part("", tuple(letters))  # "J.", "J.D.", "M-J", "JD" in "Smith JD"
    components = [c for c in re.split(rf"[-{_DASHES}]", _fold(word)) if c]
    text = "".join(c for c in "".join(components) if c.isalnum())
    if not text or text in _AUTHOR_NOISE or text in _SURNAME_PARTICLES:
        return None
    return _Part(text, tuple(c[0] for c in components))


def _parts(segment: str, grouped_initials_ok: bool) -> list[_Part]:
    out = []
    for token in re.split(r"\s+", segment.strip()):
        part = _part(token, grouped_initials_ok)
        if part:
            out.append(part)
    return out


def _parse_author(name: str) -> _Author:
    aliases = frozenset(_fold(a).strip() for a in re.findall(r"\(([^)]*)\)", name) if a.strip())
    base = re.sub(r"\([^)]*\)", " ", name).strip()
    if _INSTITUTION.search(base):
        words = [w for w in _normalize(base).split() if w not in _STOPWORDS]
        acronym = "".join(w[0] for w in words)
        return _Author((), " ".join(words), aliases | {acronym})

    readings: list[_Name] = []
    if "," in base:
        sur, giv = base.split(",", 1)
        surname = [p for p in _parts(sur, False) if p.text]
        if surname:
            readings.append(_Name(tuple(surname), tuple(_parts(giv, True))))
    else:
        tokens = base.split()
        grouped_ok = any(not t.isupper() for t in tokens)
        parts = _parts(base, grouped_ok)
        full = [i for i, p in enumerate(parts) if p.text]
        if full:
            last = full[-1]  # western: "John David Smith", "Smith JD" (initials after)
            readings.append(_Name((parts[last],), tuple(parts[:last] + parts[last + 1 :])))
            if len(parts) == 2 and len(full) == 2:  # "Wang Wei": surname may come first
                readings.append(_Name((parts[0],), (parts[1],)))
    acronyms = aliases
    if len(base.split()) == 1 and base.isupper() and base.isalpha() and 2 <= len(base) <= 6:
        acronyms = acronyms | {_fold(base)}  # a bare "WHO" may stand for an organization
    return _Author(tuple(readings), "", frozenset(acronyms))


def _same_word(a: str, b: str) -> bool:
    return a == b or (len(a) > 3 and len(b) > 3 and fuzz.ratio(a, b) >= TOKEN_SIMILARITY)


def _compatible(a: _Part, b: _Part) -> bool:
    if a.text and b.text:
        return _same_word(a.text, b.text)
    return bool(a.initials and b.initials) and a.initials[0] == b.initials[0]


def _same_person(cited: _Name, record: _Name) -> bool:
    key = cited.surname[-1]
    record_parts = list(record.surname) + list(record.given)
    hit = next((p for p in record.surname if p.text and _same_word(key.text, p.text)), None)
    if hit is None:
        return False
    remaining = [p for p in record_parts if p is not hit]
    # Other parts of a compound surname may be abbreviated in the record
    # ("Camilo da Silva" ~ "C. Silva"), but must be there.
    for extra in cited.surname[:-1]:
        match = next((p for p in remaining if _compatible(extra, p)), None)
        if match is None:
            return False
        remaining.remove(match)
    # First given names must agree when both sides have one; a missing middle
    # name or initial on either side is not a conflict.
    record_given = [p for p in remaining if p in record.given]
    if cited.given and record_given:
        return _compatible(cited.given[0], record_given[0])
    return True


def _same_author(cited: _Author, record: _Author) -> bool:
    if cited.institution or record.institution:
        both_named = cited.institution and record.institution
        if (
            both_named
            and fuzz.ratio(cited.institution, record.institution) >= SINGLE_WORD_TITLE_RATIO
        ):
            return True
        return bool(cited.acronyms & record.acronyms)
    return any(_same_person(c, r) for c in cited.readings for r in record.readings)


def _author_matches(author: str, paper: Paper) -> bool | None:
    """True if a record author is the cited first author; None if not checkable."""
    first = re.split(r";|&|\bet al\b|\band\b|\s+e\s+|\s+y\s+", author, maxsplit=1, flags=re.I)[0]
    cited = _parse_author(first)
    records = [_parse_author(a.name) for a in paper.authors if a.name.strip()]
    if not (cited.readings or cited.institution or cited.acronyms) or not records:
        return None
    return any(_same_author(cited, r) for r in records)


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
    author_ok = _author_matches(author, paper)
    year_delta = abs(paper.year - year) if year and paper.year else None
    return CitationMatch(paper, coverage, title_ok, author_ok, year_delta)


def best_match(
    papers: list[Paper], author: str, year: int | None, title_fragment: str
) -> CitationMatch | None:
    scored = [score_candidate(p, author, year, title_fragment) for p in papers]
    return max(scored, key=CitationMatch._rank, default=None)
