"""Tests for bx_scholar_core.dedup."""

from __future__ import annotations

from bx_scholar_core.dedup import deduplicate
from bx_scholar_core.models.paper import Paper


class TestDeduplicate:
    def test_dedup_by_doi(self) -> None:
        papers = [
            Paper(title="Paper A", doi="10.1234/test", year=2024, abstract="Full abstract"),
            Paper(title="Paper A", doi="10.1234/test", year=2024),
        ]
        result = deduplicate(papers)
        assert len(result) == 1
        assert result[0].abstract == "Full abstract"  # kept the richer one

    def test_dedup_by_doi_case_insensitive(self) -> None:
        papers = [
            Paper(title="Paper A", doi="10.1234/TEST"),
            Paper(title="Paper A", doi="10.1234/test"),
        ]
        result = deduplicate(papers)
        assert len(result) == 1

    def test_dedup_by_title_similarity(self) -> None:
        papers = [
            Paper(title="AI Adoption in Public Administration", year=2024),
            Paper(title="AI Adoption in Public Administrations", year=2024),
        ]
        result = deduplicate(papers)
        assert len(result) == 1

    def test_no_dedup_different_years(self) -> None:
        papers = [
            Paper(title="AI Adoption in Public Administration", year=2023),
            Paper(title="AI Adoption in Public Administration", year=2024),
        ]
        result = deduplicate(papers)
        assert len(result) == 2

    def test_no_dedup_different_titles(self) -> None:
        papers = [
            Paper(title="AI Adoption in Government", year=2024),
            Paper(title="Machine Learning in Healthcare", year=2024),
        ]
        result = deduplicate(papers)
        assert len(result) == 2

    def test_mixed_doi_and_title_close_match(self) -> None:
        papers = [
            Paper(title="AI Adoption in Public Administration", doi="10.1234/test", year=2024),
            Paper(title="AI Adoption in Public Administrations", year=2024),  # no DOI, >90% similar
        ]
        result = deduplicate(papers)
        assert len(result) == 1

    def test_mixed_doi_and_title_distant(self) -> None:
        papers = [
            Paper(title="Paper A", doi="10.1234/test", year=2024),
            Paper(title="Paper A (with extra context)", year=2024),  # no DOI, <90% similar
        ]
        result = deduplicate(papers)
        assert len(result) == 2  # not similar enough to dedup

    def test_empty_list(self) -> None:
        assert deduplicate([]) == []

    def test_single_paper(self) -> None:
        papers = [Paper(title="Only One")]
        result = deduplicate(papers)
        assert len(result) == 1

    def test_keeps_richer_metadata(self) -> None:
        papers = [
            Paper(title="Paper", doi="10.1234/a"),
            Paper(title="Paper", doi="10.1234/a", abstract="Has abstract", cited_by_count=10),
        ]
        result = deduplicate(papers)
        assert len(result) == 1
        assert result[0].abstract == "Has abstract"
