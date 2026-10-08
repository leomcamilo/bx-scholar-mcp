"""Tests for bx_scholar_core.citation_match and the verify_citation pipeline."""

from __future__ import annotations

import httpx

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
