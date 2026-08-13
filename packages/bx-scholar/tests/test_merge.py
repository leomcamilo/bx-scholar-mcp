"""Identidade única e proveniência que sobrevive ao merge."""

from __future__ import annotations

from bx_scholar.workflows.fanout import Coverage, SourceResult
from bx_scholar.workflows.merge import merge_results, rank_works

from conftest import make_paper


def results(**by_source):
    return [
        SourceResult(name, Coverage.COMPLETE, papers) for name, papers in by_source.items()
    ]


class TestDeduplication:
    def test_same_doi_across_sources_is_one_work(self) -> None:
        merged, dupes = merge_results(
            results(
                openalex=[make_paper("Estudo", doi="10.1/a")],
                crossref=[make_paper("Estudo", doi="https://doi.org/10.1/A")],
            )
        )
        assert len(merged) == 1
        assert dupes == 1

    def test_provenance_accumulates_instead_of_being_discarded(self) -> None:
        # dedup.py:42-57 do v1 ficava com o registro "mais completo" e jogava o
        # outro fora — junto com o source_api dele. Três bases independentes
        # confirmando a mesma obra viravam "veio do OpenAlex".
        merged, _ = merge_results(
            results(
                openalex=[make_paper("Estudo", doi="10.1/a")],
                crossref=[make_paper("Estudo", doi="10.1/a")],
                scielo=[make_paper("Estudo", doi="10.1/a")],
            )
        )
        assert merged[0].source_count == 3
        assert set(merged[0].seen_in) == {"openalex", "crossref", "scielo"}

    def test_merge_enriches_instead_of_choosing(self) -> None:
        # OpenAlex costuma ter o abstract; CrossRef costuma ter o ISSN. Ficar só
        # com "o mais completo" desperdiça as duas metades.
        a = make_paper("Estudo", doi="10.1/a", abstract="Resumo longo aqui")
        b = make_paper("Estudo", doi="10.1/a", cited_by=42)
        b.issn = "1234-5678"
        merged, _ = merge_results(results(openalex=[a], crossref=[b]))
        work = merged[0].paper
        assert work.abstract == "Resumo longo aqui"
        assert work.issn == "1234-5678"
        assert work.cited_by_count == 42  # fica a maior contagem

    def test_title_match_requires_same_year(self) -> None:
        merged, _ = merge_results(
            results(
                openalex=[make_paper("Um estudo sobre mobilidade", year=2020)],
                crossref=[make_paper("Um estudo sobre mobilidade", year=2015)],
            )
        )
        assert len(merged) == 2

    def test_titles_differing_only_by_accent_are_the_same_work(self) -> None:
        merged, _ = merge_results(
            results(
                scielo=[make_paper("Educação Básica no Brasil", year=2021)],
                openalex=[make_paper("Educacao Basica no Brasil", year=2021)],
            )
        )
        assert len(merged) == 1

    def test_distinct_works_stay_distinct(self) -> None:
        merged, dupes = merge_results(
            results(
                openalex=[make_paper("Estudo A", doi="10.1/a"), make_paper("Estudo B", doi="10.1/b")]
            )
        )
        assert len(merged) == 2
        assert dupes == 0


class TestRanking:
    def test_source_convergence_outranks_citations(self) -> None:
        # Convergência de fontes independentes é o sinal mais barato e mais
        # honesto de que a obra existe e é indexada de fato.
        one_source = make_paper("Muito citado", doi="10.1/a", cited_by=5000)
        merged, _ = merge_results(
            results(
                openalex=[one_source, make_paper("Convergente", doi="10.1/b", cited_by=10)],
                crossref=[make_paper("Convergente", doi="10.1/b", cited_by=10)],
                scielo=[make_paper("Convergente", doi="10.1/b", cited_by=10)],
            )
        )
        ranked = rank_works(merged)
        assert ranked[0].paper.title == "Convergente"
