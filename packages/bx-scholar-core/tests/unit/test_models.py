"""Tests for bx_scholar_core.models."""

from __future__ import annotations

import json

from bx_scholar_core.models import (
    Author,
    JournalMetrics,
    Paper,
    RankingEntry,
    RetractionStatus,
    Venue,
    VerificationResult,
)
from bx_scholar_core.models.ranking import JQLEntry, QualisEntry, SJREntry


class TestPaper:
    def test_minimal(self) -> None:
        p = Paper(title="Test Paper")
        assert p.title == "Test Paper"
        assert p.doi == ""
        assert p.authors == []
        assert p.source_type == "unknown"

    def test_doi_normalization(self) -> None:
        p = Paper(title="T", doi="https://doi.org/10.1234/test")
        assert p.doi == "10.1234/test"

    def test_doi_normalization_http(self) -> None:
        p = Paper(title="T", doi="http://doi.org/10.5678/abc")
        assert p.doi == "10.5678/abc"

    def test_doi_normalization_prefix(self) -> None:
        p = Paper(title="T", doi="doi:10.9999/xyz")
        assert p.doi == "10.9999/xyz"

    def test_doi_already_clean(self) -> None:
        p = Paper(title="T", doi="10.1234/test")
        assert p.doi == "10.1234/test"

    def test_doi_empty(self) -> None:
        p = Paper(title="T", doi="")
        assert p.doi == ""

    def test_issn_normalization_no_hyphen(self) -> None:
        p = Paper(title="T", issn="12345678")
        assert p.issn == "1234-5678"

    def test_issn_with_hyphen(self) -> None:
        p = Paper(title="T", issn="1234-5678")
        assert p.issn == "1234-5678"

    def test_issn_uppercase(self) -> None:
        p = Paper(title="T", issn="1234-567x")
        assert p.issn == "1234-567X"

    def test_issn_empty(self) -> None:
        p = Paper(title="T", issn="")
        assert p.issn == ""

    def test_full_paper(self) -> None:
        p = Paper(
            title="AI in Public Administration",
            doi="10.1234/test",
            year=2024,
            authors=[Author(name="Jane Doe"), Author(name="John Smith")],
            abstract="A study on AI adoption",
            cited_by_count=42,
            source_type="peer_reviewed",
            journal="Public Admin Review",
            issn="0033-3352",
            is_open_access=True,
            source_api="openalex",
        )
        assert p.year == 2024
        assert len(p.authors) == 2
        assert p.source_type == "peer_reviewed"

    def test_serialization_roundtrip(self) -> None:
        p = Paper(
            title="Test",
            doi="10.1234/test",
            year=2024,
            authors=[Author(name="Jane Doe", h_index=15)],
        )
        data = json.loads(p.model_dump_json())
        p2 = Paper.model_validate(data)
        assert p2.title == p.title
        assert p2.doi == p.doi
        assert p2.authors[0].name == "Jane Doe"
        assert p2.authors[0].h_index == 15


class TestAuthor:
    def test_minimal(self) -> None:
        a = Author(name="Jane Doe")
        assert a.name == "Jane Doe"
        assert a.openalex_id == ""

    def test_with_metrics(self) -> None:
        a = Author(name="Jane Doe", h_index=25, works_count=100, cited_by_count=5000)
        assert a.h_index == 25


class TestVenue:
    def test_minimal(self) -> None:
        v = Venue(name="Nature")
        assert v.name == "Nature"
        assert v.issns == []

    def test_with_issns(self) -> None:
        v = Venue(name="Nature", issn_l="0028-0836", issns=["0028-0836", "1476-4687"])
        assert len(v.issns) == 2


class TestRankingEntry:
    def test_basic(self) -> None:
        r = RankingEntry(source="sjr", value="Q1", tier="A")
        assert r.source == "sjr"
        assert r.tier == "A"


class TestJournalMetrics:
    def test_best_tier_sjr_q1(self) -> None:
        m = JournalMetrics(
            issn="0001-4273",
            sjr=SJREntry(quartile="Q1"),
        )
        assert m.best_tier == "A"

    def test_best_tier_qualis_a1(self) -> None:
        m = JournalMetrics(
            issn="0001-4273",
            qualis=QualisEntry(classification="A1"),
        )
        assert m.best_tier == "A"

    def test_best_tier_jql_4star(self) -> None:
        m = JournalMetrics(
            issn="0001-4273",
            jql=JQLEntry(abs="4*"),
        )
        assert m.best_tier == "S"

    def test_best_tier_combined(self) -> None:
        m = JournalMetrics(
            issn="0001-4273",
            sjr=SJREntry(quartile="Q2"),
            qualis=QualisEntry(classification="A1"),
            jql=JQLEntry(abs="3"),
        )
        # Q2->B, A1->A, ABS 3->B => best is A
        assert m.best_tier == "A"

    def test_best_tier_none(self) -> None:
        m = JournalMetrics(issn="0000-0000")
        assert m.best_tier == "unknown"

    def test_best_tier_low(self) -> None:
        m = JournalMetrics(
            issn="0000-0000",
            sjr=SJREntry(quartile="Q4"),
            qualis=QualisEntry(classification="B3"),
        )
        assert m.best_tier == "D"


class TestVerificationResult:
    def test_verified(self) -> None:
        v = VerificationResult(
            verified=True,
            source="crossref",
            confidence="high",
            query={"author": "Simon", "year": "1955"},
        )
        assert v.verified is True
        assert v.confidence == "high"

    def test_unverified(self) -> None:
        v = VerificationResult(verified=False, message="Not found")
        assert v.verified is False
        assert v.confidence == "none"


class TestRetractionStatus:
    def test_not_retracted(self) -> None:
        r = RetractionStatus(doi="10.1234/test")
        assert r.retracted is False

    def test_retracted(self) -> None:
        r = RetractionStatus(
            doi="10.1234/test",
            retracted=True,
            title="Retracted Paper",
            updates=[{"type": "retraction", "doi": "10.1234/retraction"}],
        )
        assert r.retracted is True
        assert len(r.updates) == 1
