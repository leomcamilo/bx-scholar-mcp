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
    def test_upstream_relevance_order_is_preserved_with_one_source(self) -> None:
        # O OpenAlex já devolve por relevância para a consulta. Reordenar por
        # citações destrói isso e faz emergir o artigo velho e muito citado em
        # vez do artigo sobre o tema perguntado — foi o que apareceu no primeiro
        # smoke contra a API real.
        merged, _ = merge_results(
            results(
                openalex=[
                    make_paper("O mais relevante", doi="10.1/a", cited_by=3),
                    make_paper("Velho e muito citado", doi="10.1/b", cited_by=5000),
                ]
            )
        )
        ranked = rank_works(merged)
        assert [w.paper.title for w in ranked] == ["O mais relevante", "Velho e muito citado"]

    def test_top_hit_in_a_smaller_base_counts(self) -> None:
        # `best_position` é o melhor posto em QUALQUER base. Uma obra que é a
        # primeira do SciELO e a quinta do OpenAlex sobe — para um produto com
        # foco brasileiro isso é recurso, não defeito: a base pequena e
        # especializada tem opinião melhor sobre o corpus dela.
        merged, _ = merge_results(
            results(
                openalex=[make_paper(f"R{i}", doi=f"10.1/{i}") for i in range(6)],
                scielo=[make_paper("R4", doi="10.1/4")],
            )
        )
        assert rank_works(merged)[0].paper.title == "R4"

    def test_boost_is_bounded_and_cannot_leapfrog_from_the_bottom(self) -> None:
        # Obra mal ranqueada em TODAS as bases não é teleportada ao topo por
        # convergência. O bônus é de algumas posições, não um veto.
        def deep():  # listas independentes: o merge muta os Paper
            return [make_paper(f"R{i}", doi=f"10.1/{i}") for i in range(25)]

        # R20 está em vigésimo lugar nas TRÊS bases — convergente, mas mal
        # ranqueado em todas elas.
        merged, _ = merge_results(results(openalex=deep(), crossref=deep(), scielo=deep()))
        ranked = [w.paper.title for w in rank_works(merged)]
        assert ranked[0] == "R0"
        assert ranked.index("R20") > 10

    def test_citations_only_break_ties(self) -> None:
        merged, _ = merge_results(
            results(
                openalex=[make_paper("A", doi="10.1/a", cited_by=10)],
                crossref=[make_paper("B", doi="10.1/b", cited_by=900)],
            )
        )
        # Ambas em posição 0 na sua fonte, ambas com 1 fonte -> desempata citação.
        assert rank_works(merged)[0].paper.title == "B"
