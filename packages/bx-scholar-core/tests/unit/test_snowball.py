"""Tests for snowballing tools — BFS orchestration and reference-line cleaning."""

from __future__ import annotations

from bx_scholar_core.models.paper import Paper
from bx_scholar_core.tools.snowball import (
    MAX_FRONTIER_PER_LEVEL,
    clean_reference_lines,
    snowball_bfs,
)


def _paper(doi: str, title: str = "", year: int = 2020, cited: int = 10) -> Paper:
    return Paper(title=title or f"Paper {doi}", doi=doi, year=year, cited_by_count=cited)


class TestCleanReferenceLines:
    def test_strips_numbering_and_bullets(self) -> None:
        text = (
            "[1] Wohlin, C. (2014). Guidelines for snowballing in systematic literature studies.\n"
            "2. Kitchenham, B. Procedures for performing systematic reviews, Keele University.\n"
            "• Page, M. J. et al. The PRISMA 2020 statement: an updated guideline.\n"
        )
        lines = clean_reference_lines(text)
        assert len(lines) == 3
        assert lines[0].startswith("Wohlin")
        assert lines[1].startswith("Kitchenham")
        assert lines[2].startswith("Page")

    def test_drops_short_lines(self) -> None:
        lines = clean_reference_lines("References\n\n[1] ok\nWohlin, C. (2014). Guidelines for snowballing in systematic studies.")
        assert len(lines) == 1


class TestSnowballBFS:
    async def test_single_level_backward(self) -> None:
        graph = {("10.1/seed", "references"): [_paper("10.1/a"), _paper("10.1/b")]}

        async def fetch(doi: str, direction: str) -> list[Paper]:
            return graph.get((doi, direction), [])

        papers, edges, stats = await snowball_bfs(
            fetch, ["10.1/seed"], ["references"], max_depth=1,
            max_papers=100, min_cited_by=0, year_from=None,
        )
        assert {p.doi for p in papers} == {"10.1/a", "10.1/b"}
        assert len(edges) == 2
        assert edges[0]["type"] == "reference"
        assert stats == [
            {"level": 1, "expanded_nodes": 1, "frontier_truncated": 0, "new_papers": 2}
        ]

    async def test_depth_two_dedups_across_levels(self) -> None:
        graph = {
            ("10.1/seed", "references"): [_paper("10.1/a")],
            ("10.1/a", "references"): [_paper("10.1/b"), _paper("10.1/a")],  # self-cycle
        }

        async def fetch(doi: str, direction: str) -> list[Paper]:
            return graph.get((doi, direction), [])

        papers, edges, stats = await snowball_bfs(
            fetch, ["10.1/seed"], ["references"], max_depth=2,
            max_papers=100, min_cited_by=0, year_from=None,
        )
        assert {p.doi for p in papers} == {"10.1/a", "10.1/b"}  # 'a' only once
        assert len(edges) == 3  # all edges recorded, including duplicate target
        assert stats[1]["new_papers"] == 1

    async def test_filters_and_cap(self) -> None:
        refs = [_paper(f"10.1/{i}", year=2010 + i, cited=i) for i in range(10)]

        async def fetch(doi: str, direction: str) -> list[Paper]:
            return refs if doi == "10.1/seed" else []

        papers, _, _ = await snowball_bfs(
            fetch, ["10.1/seed"], ["references"], max_depth=1,
            max_papers=3, min_cited_by=2, year_from=2013,
        )
        assert len(papers) == 3
        assert all(p.cited_by_count >= 2 and p.year >= 2013 for p in papers)

    async def test_both_directions_emit_typed_edges(self) -> None:
        graph = {
            ("10.1/seed", "references"): [_paper("10.1/back")],
            ("10.1/seed", "citing"): [_paper("10.1/fwd")],
        }

        async def fetch(doi: str, direction: str) -> list[Paper]:
            return graph.get((doi, direction), [])

        papers, edges, _ = await snowball_bfs(
            fetch, ["10.1/seed"], ["references", "citing"], max_depth=1,
            max_papers=100, min_cited_by=0, year_from=None,
        )
        assert {e["type"] for e in edges} == {"reference", "citation"}
        assert len(papers) == 2

    async def test_frontier_truncation_reported(self) -> None:
        big_frontier = [_paper(f"10.1/l1-{i}") for i in range(MAX_FRONTIER_PER_LEVEL + 5)]
        graph = {("10.1/seed", "references"): big_frontier}

        async def fetch(doi: str, direction: str) -> list[Paper]:
            return graph.get((doi, direction), [])

        _, _, stats = await snowball_bfs(
            fetch, ["10.1/seed"], ["references"], max_depth=2,
            max_papers=500, min_cited_by=0, year_from=None,
        )
        assert stats[1]["frontier_truncated"] == 5
