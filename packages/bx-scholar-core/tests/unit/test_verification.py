"""Unit tests for the citation verifier: titles, authors, decision, tool."""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar_core.config import Settings
from bx_scholar_core.models.paper import Author, Paper
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.registry import register_all_tools
from bx_scholar_core.verification.decide import Query, decide
from bx_scholar_core.verification.names import compare_authors
from bx_scholar_core.verification.titles import compare_title

EN = "–"  # noqa: RUF001 — en dash on purpose
EM = "—"
SUB = "Results from expert interviews"

# (given title, record title, record subtitle, expected state)
TITLE_CASES = [
    ("IL-6 effects at 6 hours", "IL-8 effects at 6 hours", "", "locate_only"),
    ("IL 6 effects at 8 hours", "IL 8 effects at 6 hours", "", "locate_only"),
    ("H2O treatment", "H2O2 treatment", "", "locate_only"),
    ("H2O treatment of soils in rural areas", "CH2O treatment of soils in rural areas", "", "locate_only"),
    ("C2H6O oxidation", "C6H2O oxidation", "", "locate_only"),
    ("Effects of IL-6 on cell growth", "Effects of IL-6R on cell growth", "", "locate_only"),
    ("COVID-19 pandemic", f"COVID{EN}19 pandemic", "", "full"),
    ("COVID-19 pandemic", "COVID 19 pandemic", "", "full"),
    ("COVID-19 pandemic", "COVID19 pandemic", "", "full"),
    ("Effects of interleukin 6 on cell growth", "Effects of interleukin-6 on cell growth", "", "full"),
    ("A 3-D model of urban growth", "A 3D model of urban growth", "", "full"),
    ("Phase I clinical trial", "Phase II clinical trial", "", "locate_only"),
    ("Hepatitis A prevalence in a rural population", "Hepatitis B prevalence in a rural population", "", "locate_only"),
    ("Tratamento com anticoagulantes em pacientes idosos", "Tratamento sem anticoagulantes em pacientes idosos", "", "locate_only"),
    ("Effects of medication on hypertension", "Effects of medication on hypotension", "", "locate_only"),
    ("Dogs bite people", "People bite dogs", "", "locate_only"),
    ("Urban mobility prediction with graph networks", "Urban mobility prediction with neural networks", "", "locate_only"),
    ("What are the limits", f"What are the limits{EM}a systematic review", "", "main"),
    ("Defining digital transformation", "Defining digital transformation", SUB, "main"),
    ("defining digital transformation results from expert interviews", "Defining digital transformation", SUB, "full"),
    ("digital transformation results from expert", "Defining digital transformation", SUB, "fragment"),
    ("digital transformation", "Defining digital transformation", SUB, "locate_only"),
    ("Leviathan", "Leviathan", "", "full"),
    ("城市交通预测", "城市交通预测研究综述", "", "fragment"),
    ("城市", "城市", "", "full"),
    ("城市交通预测COVID-19", "城市交通预测COVID-18", "", "locate_only"),
    ("Gestao publica e cidades inteligentes no Brasil", "Gestão pública e cidades inteligentes no Brasil", "", "full"),
    ("Cancer risk: 10 years of follow-up", "Cancer risk 10 years of follow-up", "", "full"),
    ("Smart cities &amp; mobility", "Smart cities & mobility", "", "full"),
]  # fmt: skip


@pytest.mark.parametrize(("given", "title", "subtitle", "expected"), TITLE_CASES)
def test_title(given: str, title: str, subtitle: str, expected: str) -> None:
    assert compare_title(given, title, subtitle).state == expected


def test_full_mode_turns_difference_into_conflict() -> None:
    assert (
        compare_title("Effects on hypotension", "Effects on hypertension", mode="full").state
        == "conflict"
    )
    assert compare_title("Effects on hypotension", "Effects on hypertension").state == "locate_only"


def _a(name: str, family: str = "", given: str = "", **kw) -> Author:
    st = "source" if family else "none"
    return Author(name=name, family=family, given=given, structure_source=st, **kw)


def _org(name: str) -> Author:
    return Author(name=name, literal=name, kind="organization", structure_source="source")


# (cited author, record authors, expected state)
AUTHOR_CASES = [
    ("John Jones", [_a("John J. Smith")], "conflict"),
    ("Smith JD", [_a("John David Smith", "Smith", "John David")], "exact"),
    ("Smith JA", [_a("John Andrew Smith", "Smith", "John Andrew")], "exact"),
    ("Smith, J.A.", [_a("John Andrew Smith", "Smith", "John Andrew")], "exact"),
    ("Smith, J. P.", [_a("John David Smith", "Smith", "John David")], "conflict"),
    ("Mill, John Stuart", [_a("John James Mill", "Mill", "John James")], "conflict"),
    ("Kim, M. P.", [_a("Min-Jun Kim", "Kim", "Min-Jun")], "conflict"),
    ("Kim M-J", [_a("Min-Jun Kim", "Kim", "Min-Jun")], "exact"),
    ("Kim, Min-Jun", [_a("Minjun Kim", "Kim", "Minjun")], "exact"),
    ("Robert Johnson", [_a("Robert Johnston", "Johnston", "Robert")], "conflict"),
    ("Camilo da Silva, L.", [_a("Leonardo C. Silva", "Silva", "Leonardo C.")], "compatible"),
    ("Silva, L.", [_a("Leonardo Camilo da Silva", "Camilo da Silva", "Leonardo")], "compatible"),
    ("da Silva, L.", [_a("Leonardo da Silva", "da Silva", "Leonardo")], "exact"),
    ("Wang Wei", [_a("W. Wang")], "compatible"),
    ("Li, Wanyan", [_a("Wei Li", "Li", "Wei")], "conflict"),
    ("Li, Wanyan", [_a("WEI Li")], "conflict"),
    ("Li, W.", [_a("Wei Li", "Li", "Wei")], "exact"),
    ("Li, W.", [_a("Wei LI")], "compatible"),
    ("García Márquez", [_a("Gabriel García Márquez")], "compatible"),
    ("University of Cambridge", [_org("University of Chicago")], "conflict"),
    ("WHO", [_a("William Henry Oswald")], "conflict"),
    ("WHO", [_org("World Health Organization")], "unknown"),
    ("WHO", [_org("World Health Organization (WHO)")], "compatible"),
    ("World Health Organization", [_org("World Health Organization")], "exact"),
    ("Instituto Brasileiro de Geografia e Estatística", [_org("Instituto Brasileiro de Geografia e Estatística")], "exact"),
    ("Silva, L.; Souza, A.", [_a("Ana Souza", "Souza", "Ana"), _a("Leo Silva", "Silva", "Leo")], "exact"),
    ("Silva, L.; Silva, L.", [_a("Leo Silva", "Silva", "Leo")], "conflict"),
    ("Mergel et al.", [_a("Ines Mergel", "Mergel", "Ines")], "exact"),
    ("Jane Doe", [_a("John Doe", "Doe", "John")], "conflict"),
    ("John Smith", [_a("J. D. Smith", "Smith", "J. D.")], "exact"),
    ("SILVA, L. C.", [_a("Leonardo Camilo Silva", "Silva", "Leonardo Camilo")], "exact"),
    ("Rosário AT", [_a("Ana Teresa Rosário", "Rosário", "Ana Teresa")], "exact"),
    ("Rosário, A.T.", [_a("Ana Teresa Rosário", "Rosário", "Ana Teresa")], "exact"),
]  # fmt: skip


@pytest.mark.parametrize(("cited", "records", "expected"), AUTHOR_CASES)
def test_author(cited: str, records: list[Author], expected: str) -> None:
    state, detail = compare_authors(cited, records)
    assert state == expected, detail


def test_author_past_the_tenth_position() -> None:
    records = [_a(f"Person {i}", f"Family{chr(97 + i)}", "Ana") for i in range(15)]
    assert compare_authors("Familym, A.", records)[0] == "exact"


def test_absent_author_in_truncated_list_is_unknown_not_conflict() -> None:
    assert (
        compare_authors("Nobody, X.", [_a("Ana Souza", "Souza", "Ana")], truncated=True)[0]
        == "unknown"
    )


# --- decision ----------------------------------------------------------------

TITLE = "Defining digital transformation"
MERGEL = Paper(
    title=TITLE,
    subtitle=SUB,
    doi="10.1016/j.giq.2019.06.002",
    year=2019,
    authors=[_a("Ines Mergel", "Mergel", "Ines"), _a("Noella Edelmann", "Edelmann", "Noella")],
)
FULL = f"{TITLE}: {SUB}"


def _decide(author: str, year: int | None, title: str, *papers: tuple[str, Paper], mode="auto"):
    return decide(Query(author, year, title, mode), list(papers))


def test_verified_high() -> None:
    d = _decide("Mergel, I.", 2019, FULL, ("crossref", MERGEL))
    assert d.status == "verified"
    assert d.best.confidence == "high"


def test_year_off_by_one_is_medium_with_warning() -> None:
    d = _decide("Mergel", 2020, FULL, ("crossref", MERGEL))
    assert d.status == "verified"
    assert d.best.confidence == "medium"
    assert any("year differs by 1" in w for w in d.best.warnings())


def test_year_off_by_two_is_conflict() -> None:
    d = _decide("Mergel", 2021, FULL, ("crossref", MERGEL))
    assert d.status == "conflict"


def test_wrong_author_on_identified_work_is_conflict() -> None:
    d = _decide("Smith, J.", 2019, FULL, ("crossref", MERGEL))
    assert d.status == "conflict"
    assert "author" in " ".join(d.best.reasons())


def test_same_title_other_authors_and_year_is_insufficient_not_conflict() -> None:
    """Found live: "Guerra dos lugares" (Rolnik 2015, a book without DOI) matched
    only a homonymous work by other authors in another year."""
    other = Paper(title="Guerra dos lugares", year=2017, authors=[_a("Ana Souza", "Souza", "Ana")])
    d = _decide("Rolnik, R.", 2015, "Guerra dos lugares", ("crossref", other))
    assert d.status == "insufficient"
    assert "same title" in d.next_action


def test_partial_title_is_insufficient() -> None:
    d = _decide("Mergel", 2019, "digital transformation", ("crossref", MERGEL))
    assert d.status == "insufficient"
    assert "full title" in d.next_action


def test_record_without_authors_is_insufficient() -> None:
    bare = MERGEL.model_copy(update={"authors": []})
    assert _decide("Mergel", 2019, FULL, ("crossref", bare)).status == "insufficient"


def test_record_without_year_is_insufficient() -> None:
    bare = MERGEL.model_copy(update={"year": None})
    assert _decide("Mergel", 2019, FULL, ("crossref", bare)).status == "insufficient"


def test_missing_field_enriched_from_same_doi() -> None:
    no_year = MERGEL.model_copy(update={"year": None})
    oa = MERGEL.model_copy(update={"authors": [_a("Ines Mergel")], "subtitle": ""})
    d = _decide("Mergel", 2019, TITLE, ("crossref", no_year), ("openalex", oa))
    assert d.status == "verified"
    assert d.best.sources == ["crossref", "openalex"]


def test_same_doi_from_two_sources_is_one_work() -> None:
    d = _decide("Mergel", 2019, FULL, ("crossref", MERGEL), ("openalex", MERGEL))
    assert d.status == "verified"


def test_two_distinct_compatible_works_are_ambiguous() -> None:
    other = MERGEL.model_copy(update={"doi": "10.9999/other"})
    d = _decide("Mergel", 2019, TITLE, ("crossref", MERGEL), ("crossref", other))
    assert d.status == "ambiguous"
    assert len(d.verifiable) == 2


def test_unrelated_hit_does_not_hide_the_right_one() -> None:
    unrelated = Paper(title="Something else entirely", year=2019, authors=[_a("X Y", "Y", "X")])
    d = _decide("Mergel", 2019, FULL, ("crossref", unrelated), ("openalex", MERGEL))
    assert d.status == "verified"


def test_full_mode_near_miss_is_conflict_unrelated_hit_is_not() -> None:
    typo = "Defining digital transformations: Results from expert interviews"
    assert _decide("Mergel", 2019, typo, ("crossref", MERGEL), mode="full").status == "conflict"
    unrelated = Paper(title="Other", year=2010, authors=[_a("X Y", "Y", "X")])
    assert (
        _decide("Mergel", 2019, typo, ("crossref", unrelated), mode="full").status == "insufficient"
    )


def test_nothing_found() -> None:
    d = _decide("Mergel", 2019, FULL)
    assert d.status == "insufficient"
    assert "does not by itself prove" in d.next_action


# --- tool --------------------------------------------------------------------

CR_ITEM = {
    "DOI": "10.1016/j.giq.2019.06.002",
    "title": [TITLE],
    "subtitle": [SUB],
    "published": {"date-parts": [[2019]]},
    "author": [{"given": "Ines", "family": "Mergel"}, {"given": "Noella", "family": "Edelmann"}],
    "type": "journal-article",
}


def _server(tmp_path, cr_handler, oa_handler):
    server = FastMCP("t")
    pool = register_all_tools(
        server,
        Settings(polite_email="ci@bxscholar.dev", cache_enabled=False),
        RankingService(data_dir=tmp_path),
    )
    pool.crossref._client = httpx.AsyncClient(transport=httpx.MockTransport(cr_handler))
    pool.openalex._client = httpx.AsyncClient(transport=httpx.MockTransport(oa_handler))
    return server, pool


async def _call(server, args) -> dict:
    out = await server.call_tool("verify_citation", args)
    return json.loads((out[0] if isinstance(out, tuple) else out)[0].text)


async def test_tool_verified_and_no_year_filter(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def cr(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"message": {"items": [CR_ITEM]}})

    server, pool = _server(tmp_path, cr, lambda r: httpx.Response(200, json={"results": []}))
    r = await _call(server, {"author": "Mergel", "year": 2019, "title_fragment": FULL})
    assert r["verified"] is True
    assert r["status"] == "verified"
    assert r["confidence"] == "high"
    assert "filter" not in seen[0].url.params  # a ±1 filter would hide year conflicts
    await pool.aclose()


async def test_tool_reports_source_failure_without_calling_it_fabricated(tmp_path) -> None:
    server, pool = _server(tmp_path, lambda r: httpx.Response(400), lambda r: httpx.Response(400))
    r = await _call(server, {"author": "Mergel", "year": 2019, "title_fragment": FULL})
    assert r["verified"] is False
    assert set(r["source_errors"]) == {"crossref", "openalex"}
    assert "fabricated" not in json.dumps(r)
    await pool.aclose()


async def test_tool_conflict_shows_closest_match(tmp_path) -> None:
    server, pool = _server(
        tmp_path,
        lambda r: httpx.Response(200, json={"message": {"items": [CR_ITEM]}}),
        lambda r: httpx.Response(200, json={"results": []}),
    )
    r = await _call(server, {"author": "Smith", "year": 2019, "title_fragment": FULL})
    assert r["status"] == "conflict"
    assert r["closest_match"]["match"]["doi"] == "10.1016/j.giq.2019.06.002"
    assert r["next_action"]
    await pool.aclose()


# --- first Codex test cycle (verifier findings 1-15) ---------------------------

CYCLE1_AUTHORS = [
    ("Smith, John P.", [_a("John D. Smith", "Smith", "John D.")], "conflict"),
    ("Li, Wanyan", [_a("WEI Li", "Li", "WEI")], "conflict"),
    ("Smith, John D.", [_a("JD Smith", "Smith", "JD")], "exact"),
    ("University of Cambridge (UC)", [_org("University of Chicago (UC)")], "conflict"),
    ("University of California (Berkeley)", [_org("University of California (Davis)")], "conflict"),
    ("Smith, J.; Smith, John", [_a("John Smith", "Smith", "John"), _a("James Smith", "Smith", "James")], "exact"),
    ("IBGE", [_org("IBGE")], "exact"),
    ("Petrobras", [_org("Petrobras")], "exact"),
    ("Wang Xiao Ming", [_a("Xiao Ming Wang", "Wang", "Xiao Ming")], "compatible"),
    ("Arruda, A. R. S.", [_a("x", "Arruda", "Angela Rebelo da Silva")], "exact"),
    ("Arruda ARDS", [_a("x", "Arruda", "Angela Rebelo da Silva")], "exact"),
    ("University of Oxford & Smith, J.", [_org("University of Oxford"), _a("John Smith", "Smith", "John")], "exact"),
    ("World Health Organization (WHO)", [_org("World Health Organization")], "exact"),
]  # fmt: skip


@pytest.mark.parametrize(("cited", "records", "expected"), CYCLE1_AUTHORS)
def test_cycle1_author(cited: str, records: list[Author], expected: str) -> None:
    assert compare_authors(cited, records)[0] == expected


@pytest.mark.parametrize(
    ("given", "title", "mode", "expected"),
    [
        ("Dynamics of x > 0 in nonlinear systems", "Dynamics of x < 0 in nonlinear systems", "full", "conflict"),
        ("IL-6 TNF-2", "Clinical effects of IL-6 TNF-2 in human tissue", "auto", "locate_only"),
        ("がん治療研究", "かん治療研究", "full", "conflict"),
        ("Effects of H2O on cell growth", "Effects of H<sub>2</sub>O on cell growth", "full", "full"),
    ],
)  # fmt: skip
def test_cycle1_title(given: str, title: str, mode: str, expected: str) -> None:
    assert compare_title(given, title, mode=mode).state == expected


def test_crossref_keeps_every_author_so_the_101st_verifies() -> None:
    """Crossref returns the whole list; cutting it at 100 turned a real co-author
    into "unknown" and a wrong one into "unknown" instead of "conflict"."""
    from bx_scholar_core.clients.crossref import _parse_item

    # surnames without digits: a digit makes a person's name unreadable
    authors = [
        {"given": "A", "family": f"F{chr(97 + i // 26)}{chr(97 + i % 26)}"} for i in range(100)
    ]
    item = {
        "title": ["T"],
        "published": {"date-parts": [[2020]]},
        "author": [*authors, {"given": "A", "family": "Target"}],
    }
    p = _parse_item(item)
    assert len(p.authors) == 101
    assert decide(Query("Target, A.", 2020, "T"), [("crossref", p)]).status == "verified"
    assert decide(Query("Nobody, A.", 2020, "T"), [("crossref", p)]).status == "conflict"


def test_search_output_trims_long_author_lists() -> None:
    from bx_scholar_core.tools.search import _dump

    p = Paper(title="T", authors=[Author(name=f"A{i}") for i in range(150)])
    d = _dump(p)
    assert len(d["authors"]) == 100
    assert d["authors_truncated"] is True


# --- second Codex test cycle -------------------------------------------------

DEEP = Author(name="DEEP Consortium", family="Consortium", given="DEEP", structure_source="source")

CYCLE2_AUTHORS = [
    ("Smith, John; ()", [_a("John Smith", "Smith", "John")], "unknown"),
    ("et al.", [_a("John Smith", "Smith", "John")], "unknown"),
    ("???", [_a("John Smith", "Smith", "John")], "unknown"),
    ("Consortium, D.", [DEEP], "conflict"),
    ("Consortium D", [DEEP], "conflict"),
    ("Consortium", [DEEP], "conflict"),
    ("DEEP Consortium", [DEEP], "exact"),
    ("University of California (IRVINE)", [_org("University of California (DAVIS)")], "conflict"),
    ("World Health Organization (WHO)", [_org("World Health Organization (WHO)")], "exact"),
    ("(张芳蕾)", [_a("张芳蕾", "张", "芳蕾")], "exact"),
]  # fmt: skip


@pytest.mark.parametrize(("cited", "records", "expected"), CYCLE2_AUTHORS)
def test_cycle2_author(cited: str, records: list[Author], expected: str) -> None:
    state, _ = compare_authors(cited, records)
    if expected == "exact":
        assert state in ("exact", "compatible")
    else:
        assert state == expected


def test_cycle2_unreadable_author_is_not_verified() -> None:
    for author in ("Smith, John; ()", "et al.", "???"):
        assert _decide(author, 2019, FULL, ("crossref", MERGEL)).status != "verified"


@pytest.mark.parametrize(
    ("given", "title", "mode", "expected"),
    [
        ("Storage at -10 °C", "Storage at 10 °C", "full", "conflict"),
        ("Storage at -10 °C", "Storage at −10 °C", "full", "full"),  # noqa: RUF001
        ("COVID-19 pandemic", "COVID 19 pandemic", "full", "full"),
        ("Effects of reform — a review", "Effects of reform &amp;mdash; a review", "full", "full"),
        ("Café culture in Paris", "Caf&amp;eacute; culture in Paris", "full", "full"),
    ],
)  # fmt: skip
def test_cycle2_title(given: str, title: str, mode: str, expected: str) -> None:
    assert compare_title(given, title, mode=mode).state == expected


def test_cycle2_distinct_dois_are_never_merged() -> None:
    reprint = MERGEL.model_copy(update={"doi": "10.1016/j.giq.2019.06.999"})
    d = _decide("Mergel", 2019, FULL, ("crossref", MERGEL), ("openalex", reprint))
    assert d.status == "ambiguous"
    john = MERGEL.model_copy(update={"authors": [_a("John Smith", "Smith", "John")]})
    james = john.model_copy(
        update={"doi": "10.9999/james", "authors": [_a("James Smith", "Smith", "James")]}
    )
    d = _decide("Smith, James", 2019, FULL, ("crossref", john), ("openalex", james))
    assert d.status == "verified" and d.best.paper.doi == "10.9999/james"


def test_cycle2_zero_padded_doi_same_record_is_one_work() -> None:
    cr = MERGEL.model_copy(update={"doi": "10.1590/s0102-311x2010.26.1.045"})
    oa = MERGEL.model_copy(update={"doi": "10.1590/S0102-311X2010.26.1.45"})
    assert _decide("Mergel", 2019, FULL, ("crossref", cr), ("openalex", oa)).status == "verified"


# --- third Codex test cycle --------------------------------------------------

SMITH = Paper(
    title="Predictive urban mobility models",
    doi="10.5555/series.01",
    year=2020,
    authors=[_a("John Smith", "Smith", "John")],
)

CYCLE3_AUTHORS = [
    ("World Health Organization (WHO-2)", [_org("World Health Organization (WHO-1)")], "conflict"),
    ("Smith, ???", [_a("John Smith", "Smith", "John")], "unknown"),
    ("Smith, John ???", [_a("John Smith", "Smith", "John")], "unknown"),
    ("Smith et al.; ???", [_a("John Smith", "Smith", "John")], "unknown"),
    ("12345", [_a("John Smith", "Smith", "John")], "unknown"),
    ("CERN", [_org("European Organization for Nuclear Research (CERN)")], "unknown"),
    ("WHO", [_org("World Health Organization (W.H.O.)")], "compatible"),
    ("Bank, R.", [_a("Randolph Bank", "Bank", "Randolph")], "exact"),
    ("Team, V.", [_a("Victoria Team", "Team", "Victoria")], "exact"),
    ("Group, D.", [_a("David Group", "Group", "David")], "exact"),
    ("Bank, R.", [_a("Randolph Bank")], "unknown"),
    ("Bank, Randolph", [_a("Randolph Bank")], "exact"),
    ("Collaboration, A.", [_a("x", "Collaboration", "Atlas"), _org("ATLAS Collaboration")], "conflict"),
    ("European Organization for Nuclear Research", [_org("European Organization for Nuclear Research (CERN)")], "compatible"),
    ("University of California (IRVINE)", [_org("University of California (DAVIS)")], "conflict"),
]  # fmt: skip


@pytest.mark.parametrize(("cited", "records", "expected"), CYCLE3_AUTHORS)
def test_cycle3_author(cited: str, records: list[Author], expected: str) -> None:
    assert compare_authors(cited, records)[0] == expected


@pytest.mark.parametrize(
    ("given", "title", "expected"),
    [
        ("Cell viability at 10 °C", "Cell viability at − 10 °C", "conflict"),  # noqa: RUF001
        ("Cell viability at .5 °C", "Cell viability at −.5 °C", "conflict"),  # noqa: RUF001
        ("Cell viability at -.5 °C", "Cell viability at -.5 °C", "full"),
        ("Stability of x1 in urban systems", "Stability of x−1 in urban systems", "conflict"),  # noqa: RUF001
        ("Escaping <i> tags in accessible documents", "Escaping <code>&lt;b&gt;</code> tags in accessible documents", "conflict"),
        ("Solutions with x ∈ A in finite systems", "Solutions with x &notin; A in finite systems", "conflict"),
        ("Café methods in urban modelling", "Caf&amp;amp;amp;eacute; methods in urban modelling", "full"),
    ],
)  # fmt: skip
def test_cycle3_title(given: str, title: str, expected: str) -> None:
    assert compare_title(given, title, mode="full").state == expected


def test_cycle3_unpadded_doi_never_fills_fields_across_works() -> None:
    no_authors = SMITH.model_copy(update={"authors": []})
    other = SMITH.model_copy(
        update={"doi": "10.5555/series.1", "title": "Rural environmental risk assessment"}
    )
    d = _decide("Smith, John", 2020, SMITH.title, ("crossref", no_authors), ("openalex", other))
    assert d.status == "insufficient"


def test_cycle3_unpadded_doi_keeps_the_right_candidate() -> None:
    james = SMITH.model_copy(
        update={"doi": "10.5555/series.1", "authors": [_a("James Smith", "Smith", "James")]}
    )
    d = _decide("Smith, James", 2020, SMITH.title, ("crossref", SMITH), ("openalex", james))
    assert d.status == "verified" and d.best.paper.doi == "10.5555/series.1"


# --- fourth Codex test cycle -------------------------------------------------

CYCLE4_AUTHORS = [
    ("Smith, John\U0001f916", [_a("John Smith", "Smith", "John")], "unknown"),
    ("Smith, 12345", [_a("John Smith", "Smith", "John")], "unknown"),
    ("Smith, John 12345", [_a("John Smith", "Smith", "John")], "unknown"),
    ("Bank, W.", [_a("World Bank")], "unknown"),
    ("World Bank", [_a("World Bank")], "exact"),
    ("DAVIS", [_org("University of California (DAVIS)")], "unknown"),
    ("WHO (WHO-2)", [_org("World Health Organization (WHO) (WHO-1)")], "conflict"),
    ("John Paul Bank", [_a("x", "Bank", "John Paul")], "exact"),
    ("Group, D. A.", [_a("x", "Group", "David Alan")], "exact"),
    ("Asian Development Bank", [_org("Asian Development Bank")], "exact"),
]  # fmt: skip


@pytest.mark.parametrize(("cited", "records", "expected"), CYCLE4_AUTHORS)
def test_cycle4_author(cited: str, records: list[Author], expected: str) -> None:
    assert compare_authors(cited, records)[0] == expected


def test_cycle4_unreadable_after_et_al_is_not_verified() -> None:
    two = MERGEL.model_copy(
        update={"authors": [_a("John Smith", "Smith", "John"), _a("Mary Jones", "Jones", "Mary")]}
    )
    author = "Smith et al.; Jones, Mary\U0001f916"
    assert _decide(author, 2019, FULL, ("crossref", two)).status != "verified"


def test_cycle4_parenthesized_romanization_is_the_name() -> None:
    sato = [_a("x", "Sato", "Takayuki")]
    assert compare_authors("(Takayuki Sato), 佐藤 孝幸", sato)[0] in ("exact", "compatible")


@pytest.mark.parametrize(
    ("given", "title", "expected"),
    [
        ("Cell viability at 10 °C", "Cell viability at - 10 °C", "conflict"),
        ("Cell viability at - 10 °C", "Cell viability at \u2212 10 °C", "full"),
        ("Cell viability at 0 5 °C", "Cell viability at 0.5 °C", "conflict"),
        ("Convergence rate x2 in iterative systems", "Convergence rate x^2 in iterative systems", "conflict"),
        ("Counting n in finite systems", "Counting n! in finite systems", "conflict"),
        ("Help in finite systems", "Help! in finite systems", "full"),
        ("Solutions with A B in finite systems", "Solutions with A \u2288 B in finite systems", "conflict"),
        ("Escaping <b> tags in accessible documents", "Escaping <code>&lt;/b&gt;</code> tags in accessible documents", "conflict"),
        ("Escaping <b> tags in accessible documents", "Escaping <code>&lt;b&gt;</code> tags in accessible documents", "full"),
        ("Accessing a b in software systems", "Accessing <code>a.b</code> in software systems", "conflict"),
        ("Escaping <i> tags in accessible documents", "Escaping &lt;code&gt;&amp;lt;b&amp;gt;&lt;/code&gt; tags in accessible documents", "conflict"),
        ("Growth 2010 - 2020 in cities", "Growth 2010\u20132020 in cities", "full"),
    ],
)  # fmt: skip
def test_cycle4_title(given: str, title: str, expected: str) -> None:
    assert compare_title(given, title, mode="full").state == expected


def test_cycle4_unpadded_doi_needs_the_same_full_title() -> None:
    sub = SMITH.model_copy(update={"subtitle": "Northern cities"})
    oa = SMITH.model_copy(update={"doi": "10.5555/series.1"})
    d = _decide("Smith, John", 2020, SMITH.title, ("crossref", sub), ("openalex", oa))
    assert d.status == "ambiguous"


# --- fifth Codex test cycle --------------------------------------------------

CYCLE5_AUTHORS = [
    ("Bank, John", [_a("Randolph Bank")], "conflict"),
    ("Bank, John", [_a("World Bank")], "conflict"),
    ("Bank, Randolph (Boston)", [_a("Randolph Bank (London)")], "conflict"),
    ("Smith, John2", [_a("John Smith", "Smith", "John")], "unknown"),
    ("Smith, 12345:", [_a("John Smith", "Smith", "John")], "unknown"),
    ("Smith, John", [_a("x", "Smith", "John\U0001f916")], "unknown"),
    ("Smith, John", [_a("x", "Smith", "12345")], "unknown"),
    ("World Health Organization (II)", [_org("World Health Organization (III)")], "conflict"),
    ("World Bank (II)", [_org("World Bank (III)")], "conflict"),
    ("Bank, John Paul", [_a("John Paul Bank")], "exact"),
    ("Councilman, John", [_a("x", "Councilman", "John")], "exact"),
    ("GROUP, DAVID", [_a("x", "Group", "DAVID")], "exact"),
]  # fmt: skip


@pytest.mark.parametrize(("cited", "records", "expected"), CYCLE5_AUTHORS)
def test_cycle5_author(cited: str, records: list[Author], expected: str) -> None:
    state = compare_authors(cited, records)[0]
    assert state in (("exact", "compatible") if expected == "exact" else (expected,))


@pytest.mark.parametrize(
    ("given", "title", "expected"),
    [
        ("Response at 5 mg in healthy adults", "Response at .5 mg in healthy adults", "conflict"),
        ("Estimating the ratio x y in populations", "Estimating the ratio x/y in populations", "conflict"),
        ("Counting 10 permutations in finite systems", "Counting 10! permutations in finite systems", "conflict"),
        ("Counting (n+1) permutations in finite systems", "Counting (n+1)! permutations in finite systems", "conflict"),
        ("Solutions of y = f(x) in dynamical systems", "Solutions of y′ = f(x) in dynamical systems", "conflict"),  # noqa: RUF001
        ("Calling exit in software systems", "Calling <code>_exit</code> in software systems", "conflict"),
        ("Cell viability at -.5 °C", "Cell viability at -5 °C", "conflict"),
        ("Computing x 2 in finite systems", "Computing x**2 in finite systems", "conflict"),
        ("Naming <code>_</code> in software systems", "Naming <code></code> in software systems", "conflict"),
        ("Accessing a.b in software systems", "Accessing <code>a.b</code> in software systems", "full"),
        ("Calling <code>foo</code> in software systems", "Calling <i><code>foo</code></i> in software systems", "full"),
        ("- 10 °C storage of cell cultures", "− 10 °C storage of cell cultures", "full"),  # noqa: RUF001
        ("U.S. policy on urban mobility", "US policy on urban mobility", "full"),
    ],
)  # fmt: skip
def test_cycle5_title(given: str, title: str, expected: str) -> None:
    assert compare_title(given, title, mode="full").state == expected
