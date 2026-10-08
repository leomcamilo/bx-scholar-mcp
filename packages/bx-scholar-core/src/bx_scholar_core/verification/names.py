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

_CJK_NAME = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]{2,}")
# An initial is a single Latin letter: a lone "张" or "안" is a name, not an initial.
_L = "[A-Za-z\u00c0-\u024f]"

# Vancouver/NLM writes every given name as an initial: "Arruda ARDS" for Ana
# Rita dos Santos Arruda.
MAX_GROUPED_INITIALS = 6


def _letters(text: str) -> str:
    return re.sub(r"[^\w]|_|\d", "", fold(text))


def _surname_key(text: str) -> list[str]:
    """Surname as comparable words: folded, hyphen and space equivalent,
    particles kept as words of their own."""
    words = re.split(r"[\s\-'.()]+", fold(text))
    return [w for w in words if w and w not in _NOISE and any(c.isalnum() for c in w)]


@dataclass(frozen=True)
class Given:
    """Given-name components: ("j", True) for an initial, ("john", False) for a name."""

    parts: tuple[tuple[str, bool], ...]
    joined: str  # all letters of the given names, for "Minjun" ~ "Min-Jun"


def _few_vowels(word: str, limit: int) -> bool:
    return sum(c in "aeiou" for c in fold(word)) <= limit


def parse_given(text: str, caps_as_initials: bool | str = True) -> Given:
    """caps_as_initials: when undotted capitals ("JD") are initials. True in a
    Vancouver block after the surname ("Oliveira AIAD"); "comma" after a comma,
    up to three letters ("Smith, JD", but "KENNEY, SYLVIA W." is a name); False
    before the surname ("WEI Li"); "no_vowel" in a source's given field."""
    parts: list[tuple[str, bool]] = []
    for raw in re.split(r"\s+", text.strip()):
        raw = raw.strip(",")
        if not raw:
            continue
        if raw.islower() and fold(raw) in PARTICLES:
            parts.append((fold(raw), False))
            continue
        if re.fullmatch(rf"(?:{_L}\.)+{_L}?\.?|{_L}(?:-{_L})+\.?|{_L}", raw) and not re.fullmatch(
            r"\w{2,}", raw
        ):
            # "J.", "J.D.", "J.-D.", "M-J", "J"
            parts.extend((c, True) for c in fold(raw) if c.isalpha())
            continue
        if (
            caps_as_initials
            and raw.isupper()
            and 2 <= len(raw) <= MAX_GROUPED_INITIALS
            and raw.isalpha()
            and (
                caps_as_initials is True  # Vancouver block after the surname: always
                or (caps_as_initials == "comma" and len(raw) <= 3)  # "Smith, JD"; not "SYLVIA"
                or (caps_as_initials == "no_vowel" and _few_vowels(raw, 0))  # source field
            )
        ):
            # Vancouver initials "JD" (only reached for the given-name slot)
            parts.extend((c, True) for c in fold(raw))
            continue
        for comp in re.split(r"-", raw):
            comp_f = _letters(comp)
            if comp_f:
                parts.append((comp_f, len(comp_f) == 1 and bool(re.fullmatch(_L, comp_f))))
    return Given(tuple(parts), "".join(p for p, initial in parts if not initial))


def _aligned(a: tuple[tuple[str, bool], ...], b: tuple[tuple[str, bool], ...]) -> bool:
    for (x, xi), (y, yi) in zip(a, b, strict=False):
        if xi or yi:
            if x[0] != y[0]:
                return False
        elif x != y:
            return False
    return True


# Particles that occur inside given names ("Angela Rebelo da Silva"). Narrower
# than PARTICLES: "Bin" or "El" can be a whole given name.
_GIVEN_PARTICLES = frozenset(
    {"da", "de", "do", "das", "dos", "del", "della", "van", "von", "der", "den", "ter", "ten",
     "la", "le", "du", "e"}
)  # fmt: skip


def _no_particles(parts: tuple[tuple[str, bool], ...]) -> tuple[tuple[str, bool], ...]:
    """Drop particles after the first component; never empty the name."""
    kept = tuple(p for i, p in enumerate(parts) if i == 0 or p[1] or p[0] not in _GIVEN_PARTICLES)
    return kept or parts


def given_compatible(a: Given, b: Given) -> bool:
    """All components present on both sides agree, position by position. Some
    styles abbreviate the particles of given names and some drop them ("A. R. S."
    and "A. R. D. S." for Angela Rebelo da Silva), so both alignments count."""
    if not a.parts or not b.parts:
        return True
    no_initials = not any(i for _, i in a.parts) and not any(i for _, i in b.parts)
    if no_initials and a.joined and a.joined == b.joined:
        return True  # "Minjun" vs "Min-Jun"; never skips an initial ("John P." vs "John D.")
    return any(
        _aligned(x, y)
        for x in (a.parts, _no_particles(a.parts))
        for y in (b.parts, _no_particles(b.parts))
    )


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
    literal: str = ""  # the whole name, folded, to compare with an organization


def _is_initials_token(tok: str, caps: bool = False) -> bool:
    """ "J.", "J.D.", "M-J", "J"; with caps=True also "JD" (only where initials
    are expected: after the surname). A lowercase particle ("e", "y") is not."""
    if tok.islower() and fold(tok) in PARTICLES:
        return False
    return bool(
        re.fullmatch(rf"(?:{_L}\.)+{_L}?\.?|{_L}(?:-{_L})+\.?|{_L}\.?", tok)
        or (caps and tok.isupper() and tok.isalpha() and len(tok) <= MAX_GROUPED_INITIALS)
    )


def parse_cited(name: str) -> CitedAuthor:
    """Readings of one cited author. "Silva, L. C." and "Smith JD" have one;
    "John Smith" has a western reading and, with only full words, an eastern
    one. A name with organization words is also read as a person, because
    sources sometimes encode a collaboration that way (family "Consortium",
    given "DEEP")."""
    aliases = _aliases(name)
    raw = re.sub(r"\([^)]*\)", " ", name).strip().strip(",;")
    if not raw or raw.startswith(","):
        # "(The NANOGrav Collaboration)", "(Takayuki Sato), 佐藤 孝幸": the
        # parenthesis is the name, not an aside
        raw = re.sub(r"[()]", " ", name).strip().strip(",;")
    literal = _org_name(name)
    if not raw:
        return CitedAuthor(name, (), "")
    if _ORG_WORDS.search(raw):
        return CitedAuthor(name, (), literal, "", aliases, literal)
    toks = raw.split()
    acronym = ""
    if len(toks) == 1 and toks[0].isupper() and toks[0].isalpha() and 2 <= len(toks[0]) <= 6:
        acronym = fold(toks[0])
    readings = tuple(r for r in _person_readings(raw) if r.surname)  # "???" names no one
    return CitedAuthor(name, readings, "", acronym, aliases, literal)


def _person_readings(raw: str) -> tuple[PersonReading, ...]:
    readings: list[PersonReading] = []
    if "," in raw:
        fam, giv = raw.split(",", 1)
        key = tuple(_surname_key(fam))
        return (PersonReading(key, parse_given(giv, "comma")),) if key else ()
    toks = raw.split()
    if len(toks) == 1:
        return (PersonReading(tuple(_surname_key(toks[0])), Given((), "")),)
    # Vancouver "Smith JD", "Silva LC", "De Almeida Marcarini E": the surname
    # (one or more words) comes first, then a block of initials. Undotted
    # capitals are initials only when the surname shows its case: in "MARIANA
    # MEDEIROS PRATES MAIA" every word is capitalized and none is an initial.
    caps_ok = any(not t.isupper() for t in toks)
    k = len(toks)
    while k > 1 and _is_initials_token(toks[k - 1], caps=caps_ok):
        k -= 1
    if 0 < k < len(toks) and not any(_is_initials_token(t) for t in toks[:k]):
        readings.append(
            PersonReading(tuple(_surname_key(" ".join(toks[:k]))), parse_given(" ".join(toks[k:])))
        )
        if k == len(toks) - 1 and toks[-1].isupper() and toks[-1].isalpha():
            # "Wei LI", "Mohammad Reza MOSAVI": the capitalized last word may be
            # the surname instead of a block of initials
            readings.append(
                PersonReading(
                    tuple(_surname_key(toks[-1])),
                    parse_given(" ".join(toks[:-1]), False),
                    secondary=True,
                )
            )
        return tuple(readings)
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
    if not any(_is_initials_token(t) for t in toks):
        # "Wang Wei", "Wang Xiao Ming": the surname may come first
        readings.append(
            PersonReading(
                tuple(_surname_key(toks[0])), parse_given(" ".join(toks[1:])), secondary=True
            )
        )
        # a bare compound surname with no given name ("García Márquez")
        readings.append(PersonReading(tuple(_surname_key(raw)), Given((), ""), secondary=True))
    return tuple(readings)


@dataclass(frozen=True)
class RecordAuthor:
    readings: tuple[PersonReading, ...]
    organization: str
    structured: bool
    aliases: frozenset[str] = frozenset()


def _is_acronym_of(acr: str, base: str) -> bool:
    """ "WHO" abbreviates "World Health Organization": its letters are initials
    of the name's words, in order (stopwords may be skipped)."""
    letters = [c for c in fold(acr) if c.isalpha()]
    initials = [w[0] for w in re.findall(r"[^\W\d_]+", fold(base))]
    it = iter(initials)
    return len(letters) >= 2 and all(any(c == x for x in it) for c in letters)


def _parenthesized_aliases(text: str) -> list[str]:
    base = re.sub(r"\([^)]*\)", " ", text)
    return [x.strip() for x in re.findall(r"\(([^)]*)\)", text) if _is_acronym_of(x, base)]


def _aliases(text: str) -> frozenset[str]:
    """Acronyms the record itself gives in parentheses: "World Health
    Organization (WHO)". A parenthesis that does not abbreviate the name is a
    qualifier ("University of California (IRVINE)")."""
    return frozenset(fold(x) for x in _parenthesized_aliases(text))


def _org_name(text: str) -> str:
    """Organization name for comparison. A parenthesized acronym is an alias and
    is set aside ("World Health Organization (WHO)"); any other parenthesis is a
    qualifier and stays ("University of California (Berkeley)")."""
    without_alias = text
    for alias in _parenthesized_aliases(text):
        without_alias = without_alias.replace(f"({alias})", " ")
    return " ".join(_surname_key(re.sub(r"[()]", " ", without_alias)))


def record_author(a: Author) -> RecordAuthor:
    if a.kind == "organization" or (a.literal and not a.family):
        text = a.literal or a.name
        return RecordAuthor((), _org_name(text), True, _aliases(text))
    if a.family and _ORG_WORDS.search(f"{a.given} {a.family}"):
        # Crossref sometimes encodes a collaboration as a person (family
        # "Consortium", given "DEEP"): it is the organization "DEEP Consortium"
        text = f"{a.given} {a.family}".strip()
        return RecordAuthor((), _org_name(text), True, _aliases(text))
    if a.family:
        family, given_text = a.family, a.given
        if "," in family and not given_text:
            # malformed source record: "Song,In-Ahm" in the family field
            family, given_text = (x.strip() for x in family.split(",", 1))
        given = parse_given(given_text, "no_vowel")
        return RecordAuthor((PersonReading(tuple(_surname_key(family)), given),), "", True)
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
        rg = _no_particles(record.given.parts)
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
        if cited.literal and cited.literal == record.organization:
            return "exact"  # "IBGE", "Petrobras": same literal name
        if cited.organization:
            # Both names are spelled out: they decide. A shared alias does not
            # make "University of Cambridge (UC)" the University of Chicago.
            return "conflict"
        if cited.acronym:
            # A bare acronym only matches an alias the record itself documents;
            # initials are never taken as proof either way.
            return "compatible" if cited.acronym in record.aliases else "unknown"
        return "conflict"
    if cited.organization and not cited.readings:
        return "conflict"
    whole = fold(re.sub(r"[\s()]+", "", cited.raw))
    if _CJK_NAME.fullmatch(whole):
        # CJK names are often cited whole, family first, no space ("张芳蕾")
        for r in record.readings:
            fam, giv = "".join(r.surname), r.given.joined
            if whole in (fam + giv, giv + fam) and giv:
                return "exact"
            if whole == fam and not giv:
                return "exact"
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


def split_cited_authors(author: str) -> list[list[str]]:
    """Possible splits of the cited author string into names.

    "Silva, L.; Souza, A." -> [["Silva, L.", "Souza, A."]]; "Mergel et al." ->
    [["Mergel"]]. " e " / " y " may join two authors ("Silva e Souza") or belong
    to a compound surname ("Da Silva e Silva"), so both splits are returned; an
    organization name is never split.
    """
    text = re.split(r"\bet\.? al\.?", author, maxsplit=1, flags=re.I)[0].strip().strip(",")
    if not text:
        return []

    def clean(parts: list[str]) -> list[str]:
        return [p.strip().strip(",") for p in parts if p.strip().strip(",")]

    if ";" in text:
        return [clean(text.split(";"))]
    if _ORG_WORDS.search(text):
        # "and"/"e" belong to names like "Food and Drug Administration"; "&" may
        # join an organization and a person ("University of Oxford & Smith, J.")
        amp = clean(re.split(r"\s*&\s*", text))
        return [[text], amp] if len(amp) > 1 else [[text]]
    strict = clean(re.split(r"\s*&\s*|\s+and\s+", text))
    loose = clean([q for p in strict for q in re.split(r"\s+e\s+|\s+y\s+", p)])
    return [strict, loose] if loose != strict else [strict]


def compare_authors(
    cited_text: str, record_authors: list[Author], truncated: bool = False
) -> tuple[AuthorState, str]:
    """Match every cited author to a distinct record author (decision 8). When
    the string splits in more than one way, the best-supported split counts."""
    splits = split_cited_authors(cited_text)
    if not splits:
        if cited_text.strip():
            # "et al." alone, or only punctuation: something was written
            return "unknown", "the cited author could not be interpreted"
        return "unknown", "no author given"
    order = {"exact": 0, "compatible": 1, "unknown": 2, "conflict": 3}
    results = [_compare_split(names, record_authors, truncated) for names in splits]
    return min(results, key=lambda r: order[r[0]])


def _compare_split(
    names: list[str], record_authors: list[Author], truncated: bool
) -> tuple[AuthorState, str]:
    cited = [parse_cited(n) for n in names]
    if any(not (c.readings or c.organization or c.acronym) for c in cited):
        # one of the names cannot be read ("Smith, John; ()"): never drop it
        return "unknown", "a cited author could not be interpreted"
    if not cited:
        # something was written but no name could be read from it: never treat
        # that as "no author given", which would skip the check
        return "unknown", "the cited author could not be interpreted"
    records = [record_author(a) for a in record_authors if a.name.strip() or a.family]
    if not records:
        return "unknown", "record has no authors"
    states = [[compare_author(c, r) for r in records] for c in cited]
    for allowed, result in ((("exact",), "exact"), (("exact", "compatible"), "compatible")):
        if _full_matching(states, allowed):
            detail = (
                "all cited authors found"
                if result == "exact"
                else "cited authors found, partially (initials, name order or compound surname)"
            )
            return result, detail  # type: ignore[return-value]
    for c, row in zip(cited, states, strict=True):
        if not any(st in ("exact", "compatible") for st in row):
            if "unknown" in row:
                # e.g. a bare acronym the record does not list: no proof either way
                return "unknown", f"{c.raw!r} cannot be checked against the record's authors"
            if truncated:
                return "unknown", f"{c.raw!r} not among the record's (truncated) authors"
            return "conflict", f"{c.raw!r} is not among the record's authors"
    return "conflict", "the cited authors cannot each be matched to a different record author"


def _full_matching(states: list[list[AuthorState]], allowed: tuple[str, ...]) -> bool:
    """Every cited author gets a distinct record author (Kuhn's augmenting paths),
    so the result never depends on the order the names were given in."""
    owner: dict[int, int] = {}

    def augment(i: int, seen: set[int]) -> bool:
        for j, st in enumerate(states[i]):
            if st in allowed and j not in seen:
                seen.add(j)
                if j not in owner or augment(owner[j], seen):
                    owner[j] = i
                    return True
        return False

    return all(augment(i, set()) for i in range(len(states)))
