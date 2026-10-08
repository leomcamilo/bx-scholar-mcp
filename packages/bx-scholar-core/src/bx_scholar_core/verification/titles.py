"""Title comparison for citation verification.

A title confirms a citation only as the full title, the main title (before a
subtitle the record itself marks) or a contiguous, same-order literal passage
of at least ``MIN_FRAGMENT_CONTENT_WORDS`` content words. Anything else only
locates candidates.

Comparison is token by token, in order, with no stopword removal ("com"/"sem"
and "with"/"without" count) and no edit distance ("hypertension" is not
"hypotension"). The only normalizations are the enumerated safe ones:
Unicode composition and width, case, whitespace, HTML entities, typographic
dashes and quotes, Latin accents, and alphanumeric units written joined or
split ("IL-6", "IL6" and "IL 6" are one unit; "IL-6R" and "H2O2" are not "IL-6"
and "H2O").
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

MIN_FRAGMENT_CONTENT_WORDS = 4
# Scripts written without spaces have no word boundaries to count; a passage
# of this many characters stands for the four-word minimum.
MIN_FRAGMENT_UNSPACED_CHARS = 6

_STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "were", "with", "without", "o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "sobre", "e", "ou", "que", "se", "ao", "aos", "à", "às", "pelo", "pela", "pelos", "pelas", "el", "la", "los", "las", "del", "y", "en", "con", "sin", "un", "una", "unos", "unas"]
)  # fmt: skip
_UNSPACED_CLASS = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
_UNSPACED = re.compile(f"[{_UNSPACED_CLASS}]")
# hyphen, non-breaking hyphen, figure dash, en dash, em dash, bar, minus,
# small em dash, small and fullwidth hyphen-minus
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
}  # fmt: skip
# A subtitle marker: ": ", or an em dash, or a dash with spaces on both sides.
_SUBTITLE = re.compile("\\s*:\\s+|\\s*\u2014\\s*|\\s+[-\u2013]\\s+")

TitleMode = Literal["auto", "full", "fragment"]
TitleMatch = Literal["full", "main", "fragment", "locate_only", "conflict", "unknown"]


def fold(text: str) -> str:
    """Safe normalization shared by titles and names."""
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    for k, v in _QUOTES.items():
        text = text.replace(k, v)
    text = re.sub(f"[{_DASHES}]", "-", text)
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", text)


def tokens(text: str) -> list[str]:
    """Ordered terms. Letter/digit runs stay whole ("h2o2", "il6", "c2h6o");
    spaceless scripts become one token per character."""
    out: list[str] = []
    for run in re.findall(r"[^\W_]+", fold(text)):
        if _UNSPACED.search(run):
            out.extend(_split_unspaced(run))
        else:
            out.append(run)
    return out


def _split_unspaced(run: str) -> list[str]:
    parts = re.findall(f"[{_UNSPACED_CLASS}]|[^{_UNSPACED_CLASS}]+", run)
    return [p for p in parts if p]


@dataclass(frozen=True)
class _Piece:
    text: str
    starts_term: bool  # first piece of an original term
    ends_term: bool  # last piece of an original term


def _pieces(text: str) -> list[_Piece]:
    """Letters and digits split apart, in order: "IL-6", "IL6" and "IL 6" all
    give il|6, "COVID-19" gives covid|19, "H2O2" gives h|2|o|2. Exact sequence
    comparison then keeps "H2O" != "H2O2" and "C2H6O" != "C6H2O"."""
    out: list[_Piece] = []
    for tok in tokens(text):
        runs = re.findall(r"\d+|[^\W\d_]+", tok) or [tok]
        for i, r in enumerate(runs):
            out.append(_Piece(r, i == 0, i == len(runs) - 1))
    return out


def _seq(text: str) -> list[str]:
    return [p.text for p in _pieces(text)]


def content_words(text: str) -> int:
    """Words that are not stopwords; a letter/number term ("IL-6") counts once."""
    return sum(1 for t in tokens(text) if t not in _STOPWORDS and not _UNSPACED.search(t))


def _contains(window: list[str], pieces: list[_Piece]) -> bool:
    """window occurs in pieces, starting and ending on term boundaries, so "H2O"
    is not found inside "CH2O" nor "IL-6" inside "IL-6R"."""
    n = len(window)
    seq = [p.text for p in pieces]
    return any(
        seq[i : i + n] == window and pieces[i].starts_term and pieces[i + n - 1].ends_term
        for i in range(len(seq) - n + 1)
    )


def _is_unspaced(text: str) -> bool:
    return bool(_UNSPACED.search(text))


def main_title(title: str, subtitle: str = "") -> str:
    """The record's main title: before an explicit subtitle field, or before a
    subtitle marker in the title itself."""
    if subtitle:
        return title
    parts = _SUBTITLE.split(title, maxsplit=1)
    return parts[0] if len(parts) > 1 else ""


@dataclass(frozen=True)
class TitleEvidence:
    state: TitleMatch
    detail: str

    @property
    def confirms(self) -> bool:
        return self.state in ("full", "main", "fragment")


def compare_title(
    given: str, title: str, subtitle: str = "", mode: TitleMode = "auto"
) -> TitleEvidence:
    """Compare the title the user gave with a record's title.

    mode "full": the user says it is the complete title, so a difference is a
    conflict. "fragment": a passage. "auto": full or main title if equal, else
    treated as a passage.
    """
    if not title.strip():
        return TitleEvidence("unknown", "record has no title")
    g = _seq(given)
    if not g:
        return TitleEvidence("locate_only", "no comparable words in the given title")
    full_text = f"{title}: {subtitle}" if subtitle else title
    full_pieces = _pieces(full_text)
    full = [p.text for p in full_pieces]
    main_text = main_title(title, subtitle)
    main = _seq(main_text) if main_text else []

    if g == full or g == _seq(title):
        # title alone equals full when there is no subtitle field
        state: TitleMatch = "full" if g == full else "main"
        return TitleEvidence(
            state, "matches the full title" if state == "full" else "matches the main title"
        )
    if main and g == main:
        return TitleEvidence("main", "matches the main title (before the subtitle)")
    if mode == "full":
        return TitleEvidence("conflict", "differs from the record title")

    if _contains(g, full_pieces):
        if _is_unspaced(given):
            chars = sum(1 for t in g if _UNSPACED.search(t))
            if chars >= MIN_FRAGMENT_UNSPACED_CHARS:
                return TitleEvidence("fragment", f"contiguous passage of {chars} characters")
            return TitleEvidence(
                "locate_only",
                f"passage shorter than {MIN_FRAGMENT_UNSPACED_CHARS} characters: give the full title",
            )
        n = content_words(given)
        if n >= MIN_FRAGMENT_CONTENT_WORDS:
            return TitleEvidence("fragment", f"contiguous passage with {n} content words")
        return TitleEvidence(
            "locate_only",
            f"passage has {n} content words (minimum {MIN_FRAGMENT_CONTENT_WORDS}): give the full title",
        )
    return TitleEvidence(
        "locate_only",
        "not a contiguous passage of the record title (words missing, changed or reordered)",
    )
