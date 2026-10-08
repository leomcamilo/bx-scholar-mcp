"""Author comparison for citation verification.

The record side uses the structure the source already provides (Crossref
family/given, Europe PMC lastName/firstName, "Last, First" catalogs). Only
records without it (OpenAlex display names) and the cited string are parsed,
into a few explicit readings; a reading is never forced to fit.

Rules (decisions 5-8 of the verification design):
- surnames must be equal after safe normalization (case, Latin accents,
  hyphen vs space); no edit distance ("Johnson" is not "Johnston"). A cited
  surname that is the trailing part of a compound record surname ("Silva" for
  "Camilo da Silva") is compatible but partial;
- every given-name component present on both sides must agree: same name, or
  an initial and a name starting with it. "John" vs "James" is a conflict;
  a component missing on one side is not;
- organizations match by name only; an acronym is never derived from initials;
- each cited author must match a distinct record author.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from bx_scholar_core.models.paper import Author
from bx_scholar_core.verification.titles import fold

PARTICLES = frozenset(
    {"da", "de", "do", "das", "dos", "di", "du", "del", "della", "der", "den", "van", "von",
     "la", "le", "y", "e", "bin", "ibn", "al", "el", "ter", "ten", "zu"}
)  # fmt: skip
_NOISE = frozenset({"jr", "sr", "ii", "iii", "iv", "eds", "ed", "org", "orgs", "coord", "et", "al"})
_ORG_WORDS = re.compile(
    r"organi[sz]ation|universi|ministr|institut|associa|societ|council|agenc|foundation|"
    r"department|commission|committee|consortium|\bcent(?:er|re)\b|\bbank\b|\boffice\b|"
    r"network|\bgroup\b|\bboard\b|academ|federation|programme|\bunion\b|administra|"
    r"collaboration|working party|\bteam\b",
    re.I,
)

AuthorState = Literal["exact", "compatible", "conflict", "unknown"]


def _letters(text: str) -> str:
    return re.sub(r"[^\w]|_|\d", "", fold(text))


def _surname_key(text: str) -> list[str]:
    """Surname as comparable words: folded, hyphen and space equivalent,
    particles kept as words of their own."""
    return [w for w in re.split(r"[\s\-'.]+", fold(text)) if w and w not in _NOISE]


@dataclass(frozen=True)
class Given:
    """Given-name components: ("j", True) for an initial, ("john", False) for a name."""

    parts: tuple[tuple[str, bool], ...]
    joined: str  # all letters of the given names, for "Minjun" ~ "Min-Jun"


def parse_given(text: str, caps_as_initials: bool = True) -> Given:
    """caps_as_initials: read "JD" as initials. True after a comma or after the
    surname (Vancouver "Smith JD"); False for words before the surname, where
    "WEI" in "WEI Li" is a name."""
    parts: list[tuple[str, bool]] = []
    for raw in re.split(r"\s+", text.strip()):
        raw = raw.strip(",")
        if not raw:
            continue
        if re.fullmatch(r"(?:\w\.)+\w?\.?|\w(?:-\w)+\.?|\w", raw) and not re.fullmatch(
            r"\w{2,}", raw
        ):
            # "J.", "J.D.", "J.-D.", "M-J", "J"
            parts.extend((c, True) for c in fold(raw) if c.isalpha())
            continue
        if caps_as_initials and raw.isupper() and 2 <= len(raw) <= 3 and raw.isalpha():
            # Vancouver initials "JD" (only reached for the given-name slot)
            parts.extend((c, True) for c in fold(raw))
            continue
        for comp in re.split(r"-", raw):
            comp_f = _letters(comp)
            if comp_f:
                parts.append((comp_f, len(comp_f) == 1))
    return Given(tuple(parts), "".join(p for p, initial in parts if not initial))


def given_compatible(a: Given, b: Given) -> bool:
    """All components present on both sides agree, position by position."""
    if not a.parts or not b.parts:
        return True
    if a.joined and b.joined and a.joined == b.joined:
        return True  # "Minjun" vs "Min-Jun"
    for (x, xi), (y, yi) in zip(a.parts, b.parts, strict=False):
        if xi or yi:
            if x[0] != y[0]:
                return False
        elif x != y:
            return False
    return True


@dataclass(frozen=True)
class PersonReading:
    surname: tuple[str, ...]
    given: Given
    # A less likely parse of an unstructured name; a match through it alone is
    # only "compatible".
    secondary: bool = False


@dataclass(frozen=True)
class CitedAuthor:
    raw: str
    readings: tuple[PersonReading, ...]
    organization: str  # folded organization name when the citation is one
    acronym: str = ""  # a bare "WHO": may name an organization, never by guessing
    aliases: frozenset[str] = frozenset()  # "(WHO)" given right in the name


def _is_initials_token(tok: str, caps: bool = False) -> bool:
    """ "J.", "J.D.", "M-J", "J"; with caps=True also "JD" (only where initials
    are expected: after the surname)."""
    return bool(
        re.fullmatch(r"(?:\w\.)+\w?\.?|\w(?:-\w)+\.?|\w\.?", tok)
        or (caps and tok.isupper() and tok.isalpha() and len(tok) <= 3)
    )


def parse_cited(name: str) -> CitedAuthor:
    """Readings of one cited author. "Silva, L. C." and "Smith JD" have one;
    "John Smith" has a western reading and, as a two-word name, an eastern one."""
    aliases = frozenset(
        fold(a).strip() for a in re.findall(r"\(([^)]*)\)", name) if a.strip().isupper()
    )
    raw = re.sub(r"\([^)]*\)", " ", name).strip().strip(",;")
    if _ORG_WORDS.search(raw):
        return CitedAuthor(name, (), " ".join(_surname_key(raw)), "", aliases)
    readings: list[PersonReading] = []
    if "," in raw:
        fam, giv = raw.split(",", 1)
        key = tuple(_surname_key(fam))
        if key:
            readings.append(PersonReading(key, parse_given(giv)))
        return CitedAuthor(name, tuple(readings), "", "", aliases)
    toks = raw.split()
    if not toks:
        return CitedAuthor(name, (), "")
    if len(toks) == 1:
        tok = toks[0]
        acronym = fold(tok) if tok.isupper() and tok.isalpha() and 2 <= len(tok) <= 6 else ""
        reading = PersonReading(tuple(_surname_key(tok)), Given((), ""))
        return CitedAuthor(name, (reading,), "", acronym, aliases)
    # Vancouver "Smith JD", "Smith J D", "Silva LC": surname first, initials after
    if not _is_initials_token(toks[0]) and all(_is_initials_token(t, caps=True) for t in toks[1:]):
        readings.append(
            PersonReading(tuple(_surname_key(toks[0])), parse_given(" ".join(toks[1:])))
        )
        if len(toks) == 2 and toks[1].isupper() and toks[1].isalpha():
            # "Wei LI": the capitalized word may be the surname instead
            readings.append(
                PersonReading(
                    tuple(_surname_key(toks[1])), parse_given(toks[0], False), secondary=True
                )
            )
        return CitedAuthor(name, tuple(readings), "", "", aliases)
    # "J. D. Smith", "John David Smith", "Leonardo Camilo da Silva", "WEI Li"
    first_full = next((i for i, t in enumerate(toks) if not _is_initials_token(t)), 0)
    j = len(toks) - 1
    while j - 1 > first_full and fold(toks[j - 1]) in PARTICLES:
        j -= 1
    readings.append(
        PersonReading(
            tuple(_surname_key(" ".join(toks[j:]))), parse_given(" ".join(toks[:j]), False)
        )
    )
    if len(toks) == 2 and not any(_is_initials_token(t) for t in toks):
        # "Wang Wei": the surname may come first
        readings.append(
            PersonReading(tuple(_surname_key(toks[0])), parse_given(toks[1]), secondary=True)
        )
    if len(toks) >= 2 and all(not _is_initials_token(t) for t in toks):
        # a bare compound surname with no given name ("García Márquez")
        readings.append(PersonReading(tuple(_surname_key(raw)), Given((), ""), secondary=True))
    return CitedAuthor(name, tuple(readings), "", "", aliases)


@dataclass(frozen=True)
class RecordAuthor:
    readings: tuple[PersonReading, ...]
    organization: str
    structured: bool
    aliases: frozenset[str] = frozenset()


def _aliases(text: str) -> frozenset[str]:
    """Acronyms the record itself gives in parentheses: "World Health Organization (WHO)"."""
    return frozenset(
        fold(x).strip() for x in re.findall(r"\(([^)]*)\)", text) if x.strip().isupper()
    )


def _org_name(text: str) -> str:
    return " ".join(_surname_key(re.sub(r"\([^)]*\)", " ", text)))


def record_author(a: Author) -> RecordAuthor:
    if a.kind == "organization" or (a.literal and not a.family):
        text = a.literal or a.name
        return RecordAuthor((), _org_name(text), True, _aliases(text))
    if a.family:
        return RecordAuthor(
            (PersonReading(tuple(_surname_key(a.family)), parse_given(a.given)),), "", True
        )
    if _ORG_WORDS.search(a.name):
        return RecordAuthor((), _org_name(a.name), False, _aliases(a.name))
    parsed = parse_cited(a.name)
    # A display name read as a bare compound surname would hide its given names.
    readings = tuple(r for r in parsed.readings if r.given.parts) or parsed.readings
    return RecordAuthor(readings, parsed.organization, False)


def _surname_state(cited: tuple[str, ...], record: tuple[str, ...]) -> AuthorState:
    """exact: same words; compatible: equal once particles are ignored, or the
    cited surname is the trailing part of a compound record surname."""
    if not cited or not record:
        return "unknown"
    if cited == record:
        return "exact"
    c = tuple(w for w in cited if w not in PARTICLES)
    r = tuple(w for w in record if w not in PARTICLES)
    if c and c == r:
        return "compatible"
    if c and len(c) < len(r) and r[-len(c) :] == c:
        return "compatible"  # "Silva" for "Camilo da Silva"
    # compound surname written with its first part abbreviated ("C. Silva")
    return "conflict"


def compare_person(cited: PersonReading, record: PersonReading) -> AuthorState:
    s = _surname_state(cited.surname, record.surname)
    given = cited.given
    if s == "conflict":
        # "Camilo da Silva, L." vs family "Silva", given "Leonardo C.": the extra
        # surname words must then line up with the record's given names.
        c = [w for w in cited.surname if w not in PARTICLES]
        r = [w for w in record.surname if w not in PARTICLES]
        extra = c[: -len(r)] if r and len(c) > len(r) and c[-len(r) :] == r else []
        rg = record.given.parts
        if extra and len(rg) >= len(extra):
            # The extra surname words sit right before the surname, so they line
            # up with the END of the record's given names ("Gabriel García" +
            # "Márquez"; "Leonardo C." + "Silva").
            tail = rg[-len(extra) :]
            if all(
                (w == p) or ((pi or len(w) == 1) and w[0] == p[0])
                for w, (p, pi) in zip(extra, tail, strict=True)
            ):
                record_given = Given(rg[: -len(extra)], "")
                if not given_compatible(cited.given, record_given):
                    return "conflict"
                return "compatible"
        return "conflict"
    if s == "unknown":
        return s
    if not given_compatible(given, record.given):
        return "conflict"
    return s


def compare_author(cited: CitedAuthor, record: RecordAuthor) -> AuthorState:
    if record.organization:
        if cited.organization:
            if cited.organization == record.organization:
                return "exact"
            if cited.aliases & record.aliases:
                return "compatible"
            return "conflict"
        if cited.acronym:
            # A bare acronym only matches an alias the record itself documents;
            # initials are never taken as proof either way.
            return "compatible" if cited.acronym in record.aliases else "unknown"
        return "conflict"
    if cited.organization:
        return "conflict"
    states: list[AuthorState] = []
    for c in cited.readings:
        for r in record.readings:
            st = compare_person(c, r)
            if st == "exact" and (c.secondary or r.secondary):
                st = "compatible"
            states.append(st)
    for best in ("exact", "compatible"):
        if best in states:
            return best  # type: ignore[return-value]
    return "conflict" if states else "unknown"


def split_cited_authors(author: str) -> list[str]:
    """ "Silva, L.; Souza, A." -> two names; "Mergel et al." -> one; an
    organization name is not split at "and"/"e"."""
    text = re.split(r"\bet\.? al\.?", author, maxsplit=1, flags=re.I)[0].strip().strip(",")
    if not text:
        return []
    if ";" in text:
        return [p.strip() for p in text.split(";") if p.strip()]
    if _ORG_WORDS.search(text):
        return [text]
    parts = re.split(r"\s*&\s*|\s+and\s+|\s+e\s+|\s+y\s+", text)
    return [p.strip().strip(",") for p in parts if p.strip().strip(",")]


def compare_authors(
    cited_text: str, record_authors: list[Author], truncated: bool = False
) -> tuple[AuthorState, str]:
    """Match every cited author to a distinct record author (decision 8)."""
    cited = [parse_cited(n) for n in split_cited_authors(cited_text)]
    cited = [c for c in cited if c.readings or c.organization]
    if not cited:
        return "unknown", "no author given"
    records = [record_author(a) for a in record_authors if a.name.strip() or a.family]
    if not records:
        return "unknown", "record has no authors"
    used: set[int] = set()
    worst: AuthorState = "exact"
    for c in cited:
        found: AuthorState | None = None
        undecided = False
        for i, r in enumerate(records):
            if i in used:
                continue
            st = compare_author(c, r)
            if st in ("exact", "compatible"):
                used.add(i)
                found = st
                break
            undecided = undecided or st == "unknown"
        if found is None:
            if undecided:
                # e.g. a bare acronym the record does not list: no proof either way
                return "unknown", f"{c.raw!r} cannot be checked against the record's authors"
            if truncated:
                return "unknown", f"{c.raw!r} not among the record's (truncated) authors"
            return "conflict", f"{c.raw!r} is not among the record's authors"
        if found == "compatible":
            worst = "compatible"
    return (
        worst,
        "all cited authors found"
        if worst == "exact"
        else "cited authors found, partially (initials, name order or compound surname)",
    )
