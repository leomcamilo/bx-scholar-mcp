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
# U+2212 MINUS SIGN is not a dash: it stays, and tokens() reads it as a minus.
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\ufe58\ufe63\uff0d"
_QUOTES = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
}  # fmt: skip
# A subtitle marker: ": ", or an em dash, or a dash with spaces on both sides.
_SUBTITLE = re.compile("\\s*:\\s+|\\s*\u2014\\s*|\\s+[-\u2013]\\s+")

TitleMode = Literal["auto", "full", "fragment"]
TitleMatch = Literal["full", "main", "fragment", "locate_only", "conflict", "unknown"]


# Presentation markup Crossref keeps in titles: H<sub>2</sub>O, <i>in vitro</i>.
# Only a complete element is markup; a lone "<b>" is text someone wrote.
_TAG_NAMES = r"sub|sup|i|b|em|strong|scp|sc|u|math|mml:[a-z]+"
_PAIRED = re.compile(rf"<({_TAG_NAMES})\b[^>]*>(.*?)</\1\s*>", re.I | re.S)
_SELF_CLOSING = re.compile(rf"<(?:{_TAG_NAMES})\b[^>]*/>", re.I)
_CODE = re.compile(r"<code\b[^>]*>(.*?)</code\s*>", re.I | re.S)
_MAX_UNESCAPE = 10
# Code spans travel through fold() between these private-use marks, so that
# tokens() keeps their punctuation ("a.b", "</b>").
CODE_OPEN, CODE_CLOSE = "\ue000", "\ue001"
# Code content is shielded from markup removal and entity decoding while the
# rest of the title is cleaned: "&", "<", ">" inside it travel as these marks.
_SHIELD = {"&": "\ue002", "<": "\ue003", ">": "\ue004"}
_UNSHIELD = {v: k for k, v in _SHIELD.items()}
# Symbols that change meaning stay as tokens: every Unicode math symbol
# (x > 0 vs x < 0, x \u2208 A vs x \u2209 A, A \u2288 B), plus %, \u00b0, ^ and primes (y').
_MATH = (
    "".join(ch for ch in map(chr, range(0x110000)) if unicodedata.category(ch) == "Sm")
    + "%\u00b0^\u2032\u2033\u2034"
)
_TERM = (
    r"[^\W_]+"
    rf"|[{re.escape(_MATH)}]"
    r"|(?<=\d)[.,](?=\d)|(?<![\w.])\.(?=\d)"  # decimals: 0.5 is not 0 5, .5 is not 5
    r"|(?<=[^\W_])\.(?=[^\W_])"  # inside a term: Node.js, a.b (initialisms set aside)
    r"|(?<=[^\W_])/(?=[^\W_])"  # x/y
    r"|(?<=[^\W_])\*+(?=[^\W_])"  # x**2
    r"|(?:(?<=\d)|(?<=\))|(?<=\b\w))!"  # factorial: n!, 10!, (n+1)! but not "Help!"
    # A minus is kept: U+2212 always ("x\u22121", "\u2212 10"); an ASCII hyphen as
    # the sign of a number ("-10", "-.5", "at - 10", but not the range "2010 -
    # 2020"). A hyphen inside a term ("COVID-19", "3-D") is not a sign.
    r"|\u2212|(?<![\w\s])-(?=\.?\d)|(?<=^)-(?= ?\.?\d)|(?<=[^\d\s] )-(?= ?\.?\d)"
)
_CODE_TERM = r"[^\W_]+|[^\w\s]|_"
# "U.S." is "US": the dots of an initialism are not part of the terms
_INITIALISM = re.compile(r"\b(?:[^\W\d_]\.){2,}")


def _shield(text: str) -> str:
    return "".join(_SHIELD.get(c, c) for c in text)


def _plain(text: str) -> str:
    """Markup removed and entities decoded until nothing changes: sources
    escape entities several times ("&amp;amp;eacute;") and escape tags
    ("&lt;b&gt;", even a whole "&lt;code&gt;" element). A <code> element is
    literal: decoded once and kept between marks, even inside <i>...</i>."""
    for _ in range(_MAX_UNESCAPE):
        cur = _CODE.sub(
            lambda m: f"{CODE_OPEN}{_shield(html.unescape(m.group(1)))}{CODE_CLOSE}", text
        )
        cur = _SELF_CLOSING.sub("", cur)
        while (nxt := _PAIRED.sub(r"\2", cur)) != cur:
            cur = nxt
        cur = html.unescape(cur)
        if cur == text:
            break
        text = cur
    return "".join(_UNSHIELD.get(c, c) for c in text)


def _is_latin_base(ch: str) -> bool:
    return ch.isascii() or "\u00c0" <= ch <= "\u024f"


def fold(text: str) -> str:
    """Safe normalization shared by titles and names: markup tags, HTML
    entities, Unicode compatibility forms, case, typographic quotes and dashes,
    and accents on Latin letters only (the dakuten in "が" is not an accent)."""
    text = _plain(text)
    text = unicodedata.normalize("NFKC", text)
    for k, v in _QUOTES.items():
        text = text.replace(k, v)
    text = re.sub(f"[{_DASHES}]", "-", text)
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", text.casefold()):
        if unicodedata.combining(ch) and out and _is_latin_base(out[-1]):
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def tokens(text: str) -> list[str]:
    """Ordered terms. Letter/digit runs stay whole ("h2o2", "il6", "c2h6o");
    spaceless scripts become one token per character."""
    out: list[str] = []
    for i, chunk in enumerate(re.split(f"[{CODE_OPEN}{CODE_CLOSE}]", fold(text))):
        if i % 2:  # inside code: every punctuation mark counts
            out.extend(re.findall(_CODE_TERM, chunk))
            continue
        # one space, as displayed: the sign rule looks at the character before
        chunk = _INITIALISM.sub(lambda m: m.group().replace(".", "") + " ", chunk)
        for run in re.findall(_TERM, re.sub(r"\s+", " ", chunk)):
            if run in ("\u2212", "-"):
                run = "-"
            elif run == ",":
                run = "."
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
    """Content words of a passage: words between spaces that are not stopwords
    or bare symbols. A hyphenated compound is one word ("large-scale",
    "4-oxo-2-butenoic"), and a number right after a word belongs to it ("IL 6"
    counts as "IL-6"), so the count never inflates past what was written."""
    n = 0
    prev_word = False
    for word in fold(text).split():
        core = re.sub(r"[^\w]|_", "", word)
        if not core or _UNSPACED.search(core):
            prev_word = False
            continue
        if core.isdigit() and prev_word:
            prev_word = False
            continue
        if core not in _STOPWORDS:
            n += 1
        prev_word = core.isalpha()
    return n


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


def main_titles(title: str, subtitle: str = "") -> list[str]:
    """Possible main titles: the title itself when the record has a separate
    subtitle field, else the text before each subtitle marker in the title
    ("L.—Preparation of X: a study" has "L." and "L.—Preparation of X")."""
    if subtitle:
        return [title]
    return [title[: m.start()] for m in _SUBTITLE.finditer(title) if title[: m.start()].strip()]


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
    mains = [_seq(m) for m in main_titles(title, subtitle)]

    if g == full or g == _seq(title):
        # title alone equals full when there is no subtitle field
        state: TitleMatch = "full" if g == full else "main"
        return TitleEvidence(
            state, "matches the full title" if state == "full" else "matches the main title"
        )
    if g in mains:
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
