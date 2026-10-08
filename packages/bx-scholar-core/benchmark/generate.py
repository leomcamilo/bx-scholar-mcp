"""Generate labeled verify_citation cases from the frozen base works.

Labels come from the annotation of each work (Crossref metadata) and from the
transformation applied, never from the verifier: this module does not import
bx_scholar_core.verification. One mutation per case; every case records the
transformation, the expected status and, when verified, the expected DOI.

Retrieval is synthetic (marked as such in each case): the simulated Crossref
and OpenAlex searches return the target's frozen records mixed with frozen
records of other works of the same stratum, in a seeded order. Cases that
need a missing or duplicated record derive it from the frozen one and say so.

    uv run python packages/bx-scholar-core/benchmark/generate.py
"""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import random
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent
WORKS = ROOT / "data" / "works"
CASES = ROOT / "data"
DISTRACTORS = 4
MIN_PASSAGE_CONTENT = 4

# Content-word specification (same list as the verifier's contract, kept as an
# independent copy so labels never come from the verifier's code).
_STOP = set(
    ["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "were", "with", "without", "o", "a", "os", "as", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "para", "por", "com", "sem", "sobre", "e", "ou", "que", "se", "ao", "aos", "à", "às", "pelo", "pela", "pelos", "pelas", "el", "la", "los", "las", "del", "y", "en", "con", "sin", "un", "una", "unos", "unas"]
)  # fmt: skip
_PARTICLES = set(
    [
        "da",
        "de",
        "do",
        "das",
        "dos",
        "di",
        "du",
        "del",
        "della",
        "der",
        "den",
        "van",
        "von",
        "la",
        "le",
        "y",
        "e",
        "bin",
        "ibn",
        "al",
        "el",
        "ter",
        "ten",
        "zu",
    ]
)
_CJK = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯]")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in s if not unicodedata.combining(c))


def _words(text: str) -> list[str]:
    return text.split()


def _content(words: list[str]) -> int:
    """Content words (specification): words between spaces that are not
    stopwords; a number right after a word belongs to it ("IL 6" = "IL-6")."""
    n = 0
    prev_word = False
    for w in words:
        core = re.sub(r"[^\w]|_", "", _fold(w))
        if not core or _CJK.search(core):
            prev_word = False
            continue
        if core.isdigit() and prev_word:
            prev_word = False
            continue
        if core not in _STOP:
            n += 1
        prev_word = core.isalpha()
    return n


def _display(text: str) -> str:
    """What a reader sees: markup removed (MathML, <i>, <sub>), spaces collapsed.
    Citations are written from this, never from the markup."""
    # Crossref double-escapes entities ("&amp;mdash;") and escapes tags ("&lt;b&gt;")
    for _ in range(3):
        text = html.unescape(_TAGS.sub("", text))
    text = _TAGS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


# Only typesetting tags: "x < 0 and y > 0" is text, not markup.
_TAGS = re.compile(r"</?(?:sub|sup|i|b|em|strong|scp|sc|u|mml:[a-z]+|math)\b[^>]*>", re.I)
# A Crossref "person" that is really a collaboration: family "Consortium",
# given "DEEP". Annotated as the organization "DEEP Consortium".
_COLLAB = re.compile(
    r"^(?:consortium|collaboration|group|committee|network|team|investigators)$", re.I
)


def _is_collab(a: dict) -> bool:
    return bool(_COLLAB.match((a.get("family") or "").strip()))


@dataclass
class Work:
    id: str
    stratum: str
    split: str
    doi: str
    title: str
    subtitle: str
    year: int | None
    persons: list[dict]  # {"family", "given"} in Crossref order
    orgs: list[str]
    crossref: dict  # raw Crossref "message"
    openalex: dict | None  # raw OpenAlex work, or None if OpenAlex had no record

    @property
    def full_title(self) -> str:
        return f"{self.title}: {self.subtitle}" if self.subtitle else self.title

    @property
    def main_title(self) -> str:
        if self.subtitle:
            return self.title
        parts = re.split(r":\s+", self.title, maxsplit=1)
        return parts[0] if len(parts) > 1 else ""


def _year(item: dict) -> int | None:
    # Bibliographic year: print, else online, else issued (fixed order, decided
    # before looking at any case).
    for key in ("published-print", "published-online", "issued", "published"):
        parts = (item.get(key) or {}).get("date-parts") or [[None]]
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def load_works() -> list[Work]:
    works = []
    for p in sorted(WORKS.glob("*.json.gz")):
        rec = json.loads(gzip.decompress(p.read_bytes()))
        item = json.loads(rec["http"]["crossref_work"]["body"])["message"]
        oa_raw = rec["http"]["openalex_work"]
        oa = json.loads(oa_raw["body"]) if oa_raw["status"] == 200 else None
        authors = item.get("author") or []
        works.append(
            Work(
                id=rec["id"],
                stratum=rec["stratum"],
                split=rec["split"],
                doi=rec["doi"],
                title=_display((item.get("title") or [""])[0]),
                subtitle=_display((item.get("subtitle") or [""])[0] or ""),
                year=_year(item),
                persons=[
                    {
                        # malformed records put a romanized name in parentheses
                        # in the family field: "(Takayuki Sato)"
                        "family": re.sub(r"[()]", "", a["family"]).strip(),
                        "given": re.sub(r"[()]", "", a.get("given") or "").strip(),
                    }
                    for a in authors
                    if a.get("family") and not _is_collab(a)
                ],
                orgs=[a["name"].strip() for a in authors if a.get("name") and not a.get("family")]
                + [
                    f"{a.get('given') or ''} {a['family']}".strip()
                    for a in authors
                    if a.get("family") and not a.get("name") and _is_collab(a)
                ],
                crossref=item,
                openalex=oa,
            )
        )
    return works


# --- citation renderings of a person ----------------------------------------


def _initials(given: str, dots: bool = True, hyphen: bool = True) -> str:
    """APA/Vancouver initials of the given names. Dotted groups give one initial
    per letter ("L.K.F." -> L. K. F.); lowercase particles are left out ("da")."""
    out = []
    for part in given.split():
        if part.islower() and _fold(part) in _PARTICLES:
            continue
        if "." in part:
            # one initial per dotted component: "L.K.F." -> L K F, "Yu." -> Y,
            # "D.R.Th." -> D R T
            comps = [c.strip("-") for c in part.split(".") if c.strip("-")]
            out.extend(c[0].upper() + ("." if dots else "") for c in comps if c[0].isalpha())
            continue
        comps = [c for c in part.split("-") if c]
        if not comps:
            continue
        sep = "-" if hyphen else ""
        letters = [c[0].upper() + ("." if dots else "") for c in comps]
        out.append(sep.join(letters))
    return (" " if dots else "").join(out)


def _latin(text: str) -> bool:
    return not _CJK.search(text)


def render(person: dict, style: str) -> str:
    fam, giv = person["family"], person["given"]
    if not _latin(fam) and not _latin(giv or fam) and style == "natural":
        return f"{fam}{giv}"
    if not _latin(fam + giv):
        # CJK names are cited whole, family first, never with initials; a mixed
        # record ("Kang Wanwan" + "康弯弯") is cited in its comma form
        if style == "natural" and _latin(fam) == _latin(giv or fam):
            return f"{fam}{giv}"
        return f"{fam}, {giv}" if giv else fam
    if not giv:
        return fam.upper() if style == "abnt" else fam
    if style == "apa":
        return f"{fam}, {_initials(giv)}"
    if style == "abnt":
        return f"{fam.upper()}, {giv}"
    if style == "vancouver":
        return f"{fam} {_initials(giv, dots=False, hyphen=False)}"
    return f"{giv} {fam}"  # natural order


@dataclass
class Case:
    id: str
    work: str
    split: str
    stratum: str
    transformation: str
    author: str
    year: int | None
    title: str
    title_mode: str
    expected_status: str
    expected_doi: str = ""
    expected_confidence: str = ""
    retrieval: dict = field(default_factory=dict)
    synthetic: list[str] = field(default_factory=list)


def _retrieval(w: Work, pool: list[Work], rng: random.Random, **flags) -> dict:
    """Which frozen records the simulated searches return, in order. Distractors
    never share the target's title (that would make an unplanned ambiguity)."""
    target = _word_seq(w.full_title)
    eligible = [
        o
        for o in pool
        if o.id != w.id
        and not _is_window(_word_seq(o.title), target)
        and not _is_window(target, _word_seq(o.full_title))
    ]
    others = [o.id for o in rng.sample(eligible, min(DISTRACTORS, len(eligible)))]
    ids = [w.id, *others]
    rng.shuffle(ids)
    if flags.get("empty"):
        ids = []
    return {"crossref": ids, "openalex": list(ids), **{k: v for k, v in flags.items() if v}}


def _surname_words(family: str) -> set[str]:
    return {w for w in re.split(r"[\s\-'.]+", _fold(family)) if w and w not in _PARTICLES}


def _stranger_ok(w: Work, surname: str) -> bool:
    """A substitute surname shares no word with any surname of the work, so it
    cannot be an allowed variant ("Silva" for "da Silva")."""
    words = _surname_words(surname)
    return bool(words) and all(not (words & _surname_words(p["family"])) for p in w.persons)


def _word_seq(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", _fold(text))


def _is_window(part: list[str], whole: list[str]) -> bool:
    n = len(part)
    return bool(part) and any(whole[i : i + n] == part for i in range(len(whole) - n + 1))


def _surname_pool(works: list[Work]) -> list[str]:
    return sorted({p["family"] for w in works for p in w.persons if len(p["family"]) > 2})


def _has_family(w: Work, family: str) -> bool:
    return any(_fold(p["family"]) == _fold(family) for p in w.persons)


def generate(works: list[Work]) -> list[Case]:
    by_stratum: dict[str, list[Work]] = {}
    for w in works:
        by_stratum.setdefault(w.stratum, []).append(w)
    surnames = _surname_pool(works)
    org_names = sorted({o for w in works for o in w.orgs})
    cases: list[Case] = []

    for w in works:
        rng = random.Random(int(hashlib.sha256(w.id.encode()).hexdigest(), 16))
        pool = [o for o in by_stratum[w.stratum] if o.id != w.id] or [
            o for o in works if o.id != w.id
        ]
        n = 0

        def add(
            kind: str,
            author: str,
            year: int | None,
            title: str,
            mode: str,
            status: str,
            conf: str = "",
            synthetic: list[str] | None = None,
            *,
            w: Work = w,
            pool: list[Work] = pool,
            rng: random.Random = rng,
            **retrieval_flags,
        ) -> None:
            nonlocal n
            n += 1
            cases.append(Case(
                id=f"{w.id}#{n:02d}", work=w.id, split=w.split, stratum=w.stratum,
                transformation=kind, author=author, year=year, title=title, title_mode=mode,
                expected_status=status,
                expected_doi=w.doi if status == "verified" else "",
                expected_confidence=conf,
                retrieval=_retrieval(w, pool, rng, **retrieval_flags),
                synthetic=["retrieval"] + (synthetic or []),
            ))  # fmt: skip

        if w.year is None:
            continue
        first = render(w.persons[0], "apa") if w.persons else (w.orgs[0] if w.orgs else "")
        full, y = w.full_title, w.year

        # --- positives ---------------------------------------------------
        if w.persons:
            p0 = w.persons[0]
            for style in ("apa", "abnt", "vancouver", "natural"):
                add(f"author_{style}", render(p0, style), y, full, "auto", "verified")
            add("et_al", f"{p0['family']} et al.", y, full, "full", "verified")
            if len(w.persons) > 1:
                k = 10 if len(w.persons) > 10 else rng.randrange(1, len(w.persons))
                add("coauthor" + ("_after_10th" if k >= 10 else ""), render(w.persons[k], "apa"),
                    y, full, "auto", "verified")  # fmt: skip
                add("two_authors_reordered",
                    f"{render(w.persons[k], 'apa')}; {render(p0, 'apa')}", y, full, "auto",
                    "verified")  # fmt: skip
        elif w.orgs:
            add("org_name", w.orgs[0], y, full, "auto", "verified")
        add("year_plus_1", first, y + 1, full, "auto", "verified", conf="medium")
        if w.main_title and _content(_words(w.main_title)) >= 1:
            add("main_title", first, y, w.main_title, "auto", "verified")
        add("lowercase", first, y, full.lower(), "auto", "verified")
        folded = "".join(
            c for c in unicodedata.normalize("NFKD", full) if not unicodedata.combining(c)
        )
        if folded != full and not _CJK.search(full):
            add("accents_removed", first, y, folded, "auto", "verified")
        words = _words(full)
        if not _CJK.search(full) and _content(words) >= MIN_PASSAGE_CONTENT + 1:
            # a literal passage: drop the first word (or two), keep >= 4 content words
            for drop in (1, 2):
                passage = words[drop:]
                text = " ".join(passage)
                if _content(passage) >= MIN_PASSAGE_CONTENT and text not in (full, w.main_title):
                    add("passage_4plus", first, y, text, "auto", "verified")
                    break

        # --- insufficient ------------------------------------------------
        if not _CJK.search(full) and _content(words) >= 4:
            short = [x for x in words if re.sub(r"[^\w]", "", _fold(x)) not in _STOP][1:3]
            text = " ".join(short)
            if text and " ".join(short) not in (full, w.main_title) and _content(short) < 4:
                add("passage_short", first, y, text, "auto", "insufficient")
        inner = [i for i in range(1, len(words) - 1) if re.search(r"\w", words[i])]
        if not _CJK.search(full) and len(words) >= 5 and inner:
            # drop a real word (not a lone dash), so the term sequence changes
            i = rng.choice(inner)
            gap = words[:i] + words[i + 1 :]
            gap_seq = _word_seq(" ".join(gap))
            # skip when the deletion still leaves an allowed passage ("very very")
            if not _is_window(gap_seq, _word_seq(full)) and gap_seq != _word_seq(w.main_title):
                add("words_not_contiguous", first, y, " ".join(gap), "auto", "insufficient")
        add("nothing_found", first, y, full, "auto", "insufficient", empty=True)
        if w.persons:
            add("record_without_authors", first, y, full, "auto", "insufficient",
                synthetic=["authors removed from both records"], strip_authors=True)  # fmt: skip
        if w.orgs:
            acronym = "".join(x[0] for x in w.orgs[0].split() if x[0].isupper())
            # never an acronym that is also a surname in the work ("TA" vs a Ta)
            if (
                2 <= len(acronym) <= 6
                and not _has_family(w, acronym)
                and f"({acronym})" not in w.orgs[0]
            ):
                add("org_bare_acronym", acronym, y, full, "auto", "insufficient")

        # --- conflicts ---------------------------------------------------
        add("year_plus_3", first, y + 3, full, "auto", "conflict")
        if w.persons:
            p0 = w.persons[0]
            stranger = next(
                (x for x in rng.sample(surnames, len(surnames)) if _stranger_ok(w, x)), None
            )
            if stranger:
                add("author_swapped", f"{stranger}, {_initials(p0['given']) or 'A.'}", y, full,
                    "auto", "conflict")  # fmt: skip
                add("author_appended_stranger", f"{render(p0, 'apa')}; {stranger}, B.", y,
                    full, "auto", "conflict")  # fmt: skip
            if p0["given"]:
                # initials taken by anyone whose surname could be read as p0's
                fam = _surname_words(p0["family"])
                related = [p for p in w.persons if _surname_words(p["family"]) & fam]
                used = {_fold(p["given"])[:1] for p in related if p["given"]}
                wrong = next((c for c in "zqxkwvjy" if c not in used), None)
                if wrong:
                    add("initial_conflict", f"{p0['family']}, {wrong.upper()}.", y, full,
                        "auto", "conflict")  # fmt: skip
        elif w.orgs:
            # no organization that already authors this work (some list several)
            mine = {_fold(o) for o in w.orgs} | {_fold(p["family"]) for p in w.persons}
            others = [o for o in org_names if _fold(o) not in mine]
            if others:
                add("org_other", rng.choice(others), y, full, "auto", "conflict")
        if not _CJK.search(full):
            content_idx = [
                i for i, x in enumerate(words)
                if sum(ch.isalpha() for ch in x) > 4 and re.sub(r"[^\w]", "", _fold(x)) not in _STOP
            ]  # fmt: skip
            if content_idx:
                i = rng.choice(content_idx)
                wd = words[i]
                j = next(k for k, ch in enumerate(wd) if ch.isalpha())
                typo = wd[:j] + ("x" if wd[j].lower() != "x" else "z") + wd[j + 1 :]
                t_words = [*words[:i], typo, *words[i + 1 :]]
                add("typo_full_mode", first, y, " ".join(t_words), "full", "conflict")
                add("typo_auto_mode", first, y, " ".join(t_words), "auto", "insufficient")
            digits = [i for i, x in enumerate(words) if re.search(r"\d", x)]
            if digits:
                i = digits[0]
                bumped = re.sub(r"\d+", lambda m: str(int(m.group()) + 1), words[i], count=1)
                add("number_changed_full_mode", first, y,
                    " ".join([*words[:i], bumped, *words[i + 1 :]]), "full", "conflict")  # fmt: skip
            if len(content_idx) >= 2:
                a, b = content_idx[0], content_idx[-1]
                if _fold(words[a]) != _fold(words[b]):
                    sw = list(words)
                    sw[a], sw[b] = sw[b], sw[a]
                    add("order_swapped_full_mode", first, y, " ".join(sw), "full", "conflict")

        # --- ambiguous ---------------------------------------------------
        add("duplicate_work_other_doi", first, y, full, "auto", "ambiguous",
            synthetic=["a second record with the same metadata and another DOI"],
            duplicate=True)  # fmt: skip

    return cases


def main() -> None:
    works = load_works()
    cases = generate(works)
    for split in ("dev", "holdout"):
        out = CASES / f"cases_{split}.jsonl.gz"
        with gzip.open(out, "wt", encoding="utf-8") as f:
            for c in cases:
                if c.split == split:
                    f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    by: dict[str, int] = {}
    for c in cases:
        by[c.expected_status] = by.get(c.expected_status, 0) + 1
    print(f"{len(works)} works, {len(cases)} cases: {by}")


if __name__ == "__main__":
    main()
