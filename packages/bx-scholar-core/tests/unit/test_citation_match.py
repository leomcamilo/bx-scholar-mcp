"""Tests for bx_scholar_core.citation_match and the verify_citation pipeline."""

from __future__ import annotations

import httpx
import pytest

from bx_scholar_core.citation_match import best_match, score_candidate
from bx_scholar_core.clients.crossref import CrossRefClient
from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.models.paper import Author, Paper
from bx_scholar_core.tools.verify import _verify_one


def _paper(title: str, authors: list[str], year: int | None = 2020, doi: str = "") -> Paper:
    return Paper(title=title, authors=[Author(name=a) for a in authors], year=year, doi=doi)


DIGITAL_GOV = _paper(
    "Digital transformation in the public sector: a systematic review",
    ["Ines Mergel", "Noella Edelmann"],
    2019,
)


class TestScoreCandidate:
    def test_exact_citation_is_high(self) -> None:
        m = score_candidate(DIGITAL_GOV, "Mergel", 2019, "digital transformation public sector")
        assert m.verified
        assert m.confidence == "high"

    def test_wrong_author_rejected(self) -> None:
        """Real title attributed to someone else: the classic hallucination."""
        m = score_candidate(DIGITAL_GOV, "Rodrigues", 2019, "digital transformation public sector")
        assert m.title_ok
        assert not m.verified
        assert m.confidence == "low"
        assert any("author" in r for r in m.reasons())

    def test_shared_stopwords_do_not_match(self) -> None:
        """Old rule: >2 shared words, stopwords included, counted as a match."""
        hit = _paper("The role of the state in the economy", ["Mergel"], 2019)
        m = score_candidate(hit, "Mergel", 2019, "the role of AI in the workplace")
        assert not m.verified

    def test_year_off_by_one_is_medium(self) -> None:
        m = score_candidate(DIGITAL_GOV, "Mergel", 2020, "digital transformation public sector")
        assert m.verified
        assert m.confidence == "medium"

    def test_year_off_by_two_rejected(self) -> None:
        m = score_candidate(DIGITAL_GOV, "Mergel", 2021, "digital transformation public sector")
        assert not m.verified

    def test_accents_and_compound_brazilian_surname(self) -> None:
        hit = _paper("Gestão pública e cidades inteligentes", ["Leonardo Camilo da Silva"], 2024)
        m = score_candidate(hit, "Silva, L. C.", 2024, "gestao publica cidades inteligentes")
        assert m.verified
        assert m.author_ok is True

    def test_first_author_taken_from_list(self) -> None:
        m = score_candidate(
            DIGITAL_GOV, "Mergel; Edelmann", 2019, "digital transformation public sector"
        )
        assert m.author_ok is True

    def test_typo_tolerated(self) -> None:
        m = score_candidate(DIGITAL_GOV, "Mergel", 2019, "digital transformaton public sectors")
        assert m.verified

    def test_record_without_authors_caps_at_medium(self) -> None:
        hit = _paper(DIGITAL_GOV.title, [], 2019)
        m = score_candidate(hit, "Mergel", 2019, "digital transformation public sector")
        assert m.verified
        assert m.author_ok is None
        assert m.confidence == "medium"

    def test_single_word_fragment_needs_whole_title(self) -> None:
        assert not score_candidate(DIGITAL_GOV, "Mergel", 2019, "digital").verified
        leviathan = _paper("Leviathan", ["Thomas Hobbes"], 1651)
        assert score_candidate(leviathan, "Hobbes", 1651, "Leviathan").verified


class TestBestMatch:
    def test_picks_matching_candidate_not_first(self) -> None:
        decoy = _paper("Digital public services in Europe", ["Someone Else"], 2019)
        m = best_match([decoy, DIGITAL_GOV], "Mergel", 2019, "digital transformation public sector")
        assert m is not None
        assert m.paper is DIGITAL_GOV

    def test_empty(self) -> None:
        assert best_match([], "Mergel", 2019, "anything") is None


def _client(cls, payload):
    client = cls(polite_email="test@uni.edu")
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    )
    return client


UNRELATED_OA = {
    "id": "https://openalex.org/W1",
    "title": "Deep learning for protein folding",
    "doi": "https://doi.org/10.9/x",
    "publication_year": 2019,
    "type": "article",
    "authorships": [{"author": {"display_name": "Ana Souza", "id": "A1"}}],
}


class TestVerifyOne:
    async def test_openalex_fallback_no_longer_accepts_any_hit(self) -> None:
        """Before: the first OpenAlex result was returned as verified, unchecked."""
        cr = _client(CrossRefClient, {"message": {"items": []}})
        oa = _client(OpenAlexClient, {"results": [UNRELATED_OA], "meta": {"count": 1}})
        try:
            r = await _verify_one(cr, oa, "Mergel", 2019, "digital transformation public sector")
        finally:
            await cr.close()
            await oa.close()
        assert r["verified"] is False
        assert r["closest_match"]["source"] == "openalex"
        assert r["closest_match"]["rejected_because"]

    async def test_crossref_hit_skips_openalex(self) -> None:
        item = {
            "DOI": "10.1/dg",
            "title": [DIGITAL_GOV.title],
            "published-print": {"date-parts": [[2019]]},
            "author": [{"given": "Ines", "family": "Mergel"}],
            "type": "journal-article",
        }
        cr = _client(CrossRefClient, {"message": {"items": [item]}})
        oa = OpenAlexClient(polite_email="test@uni.edu")
        oa._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(AssertionError))
        )
        try:
            r = await _verify_one(cr, oa, "Mergel", 2019, "digital transformation public sector")
        finally:
            await cr.close()
            await oa.close()
        assert r["verified"] is True
        assert r["source"] == "crossref"
        assert r["confidence"] == "high"
        assert r["match"]["doi"] == "10.1/dg"


class TestCodexReviewRegressions:
    """Findings from the Codex (gpt-6-astra) review of 2026-10-08."""

    T = "Urban mobility prediction with graph networks"
    F = "urban mobility prediction graph networks"

    def test_shared_given_name_is_not_an_author_match(self) -> None:
        m = score_candidate(_paper(self.T, ["John Smith"]), "John Jones", 2020, self.F)
        assert m.author_ok is False
        assert not m.verified

    def test_short_surname_is_checked_not_skipped(self) -> None:
        assert score_candidate(_paper(self.T, ["Wei Zhang"]), "Li", 2020, self.F).author_ok is False
        assert score_candidate(_paper(self.T, ["Wei Li"]), "Li", 2020, self.F).author_ok is True

    def test_surname_forms(self) -> None:
        paper = _paper(self.T, ["Leonardo Camilo da Silva", "John Smith"])
        for cited in ("Smith J", "J. Smith", "Smith, J.", "da Silva, L.", "Silva; Souza"):
            assert score_candidate(paper, cited, 2020, self.F).author_ok is True, cited

    def test_different_cjk_titles_rejected(self) -> None:
        m = score_candidate(_paper("蛋白质结构", ["王伟"]), "王伟", 2020, "城市交通")
        assert not m.verified
        assert m.title_coverage == 0.0

    def test_cjk_fragment_of_title_accepted(self) -> None:
        m = score_candidate(_paper("城市交通预测研究", ["王伟"]), "王伟", 2020, "城市交通")
        assert m.verified

    def test_numbered_terms_must_match_exactly(self) -> None:
        il8 = _paper("Effects of IL-8 on cell growth", ["Ana Souza"])
        assert not score_candidate(il8, "Souza", 2020, "Effects of IL-6 on cell growth").verified
        il6 = _paper("Effects of IL6 on cell growth", ["Ana Souza"])
        assert score_candidate(il6, "Souza", 2020, "Effects of IL-6 on cell growth").verified

    def test_short_acronyms_must_match_exactly(self) -> None:
        ar = _paper("AR in urban planning education", ["Ana Souza"])
        assert not score_candidate(ar, "Souza", 2020, "AI in urban planning education").verified

    def test_fragment_of_only_stopwords_never_matches(self) -> None:
        assert not score_candidate(_paper("The", ["Ana Souza"]), "Souza", 2020, "the of").verified


T2 = "Urban mobility prediction with graph networks"
F2 = "urban mobility prediction graph networks"

# (case, record title, record authors, cited author, title fragment, verified?)
SECOND_REVIEW_CASES = [
    # discriminant terms must match in every route (finding 4, still open after round 1)
    ("IL-6 vs IL-8, long title", "Effects of IL-8 on human cell growth", ["Ana Souza"], "Souza",
     "Effects of IL-6 on human cell growth", False),
    ("AI vs AR, long title", "AR methods for urban planning education in schools", ["Ana Souza"],
     "Souza", "AI methods for urban planning education in schools", False),
    ("BRCA123456 vs BRCA123457", "BRCA123457", ["Ana Souza"], "Souza", "BRCA123456", False),
    ("CJK + number", "城市交通预测COVID-18", ["王伟"], "王伟", "城市交通预测COVID-19", False),
    ("COVID-19 vs COVID-18", "COVID-18 pandemic", ["Ana Souza"], "Souza", "COVID-19 pandemic", False),
    # false negatives introduced by round 1
    ("grouped initials", T2, ["John David Smith"], "Smith JD", F2, True),
    ("eastern order", T2, ["W. Wang"], "Wang Wei", F2, True),
    ("corporate with acronym", T2, ["World Health Organization"],
     "World Health Organization (WHO)", F2, True),
    ("bare corporate acronym", T2, ["World Health Organization"], "WHO", F2, True),
    ("abbreviated compound surname", T2, ["Leonardo C. Silva"], "Camilo da Silva, L.", F2, True),
    ("en dash", "COVID–19 pandemic", ["Ana Souza"], "Souza", "COVID-19 pandemic", True),  # noqa: RUF001
    ("space before number", "COVID 19 pandemic", ["Ana Souza"], "Souza", "COVID-19 pandemic", True),
    ("short identical CJK", "城市", ["王伟"], "王伟", "城市", True),
    ("main title without subtitle", "What are the limits: a systematic review", ["Ana Souza"],
     "Souza", "What are the limits", True),
    # guarantees that must keep holding
    ("shared given name", T2, ["John Smith"], "John Jones", F2, False),
    ("different given name", T2, ["John Doe"], "Jane Doe", F2, False),
    ("short surname absent", T2, ["Wei Zhang"], "Li", F2, False),
    ("short surname present", T2, ["Wei Li"], "Li", F2, True),
    ("full given vs initial", T2, ["Smith, J."], "Smith, John", F2, True),
    ("accented surname", T2, ["Maria Gonçalves"], "Goncalves, M.", F2, True),
    ("et al.", T2, ["Ines Mergel", "N Edelmann"], "Mergel et al.", F2, True),
    ("IL6 written without hyphen", "Effects of IL6 on cell growth", ["Ana Souza"], "Souza",
     "Effects of IL-6 on cell growth", True),
    ("acronyms only", "AI ML NLP", ["Ana Souza"], "Souza", "AI ML NLP", True),
    ("single word is not the title", "Digital transformation in the public sector",
     ["Ines Mergel"], "Mergel", "digital", False),
]  # fmt: skip


@pytest.mark.parametrize(
    ("title", "authors", "cited", "fragment", "expected"),
    [c[1:] for c in SECOND_REVIEW_CASES],
    ids=[c[0] for c in SECOND_REVIEW_CASES],
)
def test_second_codex_review(title, authors, cited, fragment, expected) -> None:
    m = score_candidate(_paper(title, authors, 2020), cited, 2020, fragment)
    assert m.verified is expected, (m.checks(), m.reasons())
