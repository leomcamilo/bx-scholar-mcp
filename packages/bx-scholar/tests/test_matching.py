"""Score de casamento bibliográfico — computado e devolvido, não constante.

O v1 calculava `title_match` em crossref.py:126-133 e o descartava, devolvendo
`confidence: "high"` fixo por branch. Duas referências, uma exata e uma com
autor e ano trocados, saíam com a mesma "confiança".
"""

from __future__ import annotations

from bx_scholar.workflows.matching import (
    BibliographicStatus,
    CitedReference,
    parse_reference,
    pick_best,
    resolve_by_doi,
    score_candidate,
)
from conftest import make_paper


def ref(**kw) -> CitedReference:
    return CitedReference(**kw)


class TestScoring:
    def test_exact_match_scores_high(self) -> None:
        r = ref(title="Mobilidade urbana e saúde", authors=["Silva"], year=2020)
        c = make_paper("Mobilidade urbana e saúde", year=2020)
        assert score_candidate(r, c).total >= 85

    def test_wrong_year_and_author_scores_lower_than_exact(self) -> None:
        # O ponto da fase: estes DOIS não podem sair com a mesma confiança.
        r = ref(title="Mobilidade urbana e saúde", authors=["Silva"], year=2020)
        exact = make_paper("Mobilidade urbana e saúde", year=2020)
        off = make_paper("Mobilidade urbana e saúde", year=2011)
        assert score_candidate(r, exact).total > score_candidate(r, off).total

    def test_one_year_off_is_tolerated(self) -> None:
        # Online-first e ahead-of-print produzem 1 ano de diferença em
        # referência legítima o tempo todo.
        r = ref(title="Estudo sobre X", year=2020)
        assert score_candidate(r, make_paper("Estudo sobre X", year=2021)).year == 75.0
        assert score_candidate(r, make_paper("Estudo sobre X", year=2014)).year == 0.0

    def test_surname_matching_not_full_name(self) -> None:
        # "Silva, J." na referência vs "João Pedro da Silva" no registro.
        r = ref(title="Estudo sobre X", authors=["Silva"])
        c = make_paper("Estudo sobre X")
        c.authors[0].name = "João Pedro da Silva"
        assert score_candidate(r, c).author == 100.0

    def test_components_are_exposed_separately(self) -> None:
        r = ref(title="Estudo sobre X", authors=["Silva"], year=2020)
        d = score_candidate(r, make_paper("Estudo sobre X", year=2020)).as_dict()
        assert set(d["components"]) == {"title", "year", "author"}
        assert d["method"] == "bibliographic"


class TestPickBest:
    def test_strong_single_candidate_is_verified(self) -> None:
        r = ref(title="Mobilidade urbana e saúde coletiva", year=2020)
        res = pick_best(r, [make_paper("Mobilidade urbana e saúde coletiva", year=2020)])
        assert res.status is BibliographicStatus.VERIFIED
        assert res.score is not None

    def test_two_equivalent_candidates_are_ambiguous_not_the_first(self) -> None:
        # Devolver o primeiro de dois indistinguíveis é como se atribui a
        # citação ao artigo errado com aparência de certeza.
        r = ref(title="Estudo sobre mobilidade urbana", year=2020)
        res = pick_best(
            r,
            [
                make_paper("Estudo sobre mobilidade urbana", year=2020),
                make_paper("Estudo sobre mobilidade urbana", year=2020, journal="Outra Revista"),
            ],
        )
        assert res.status is BibliographicStatus.AMBIGUOUS
        assert res.runner_up is not None

    def test_weak_candidates_are_not_found(self) -> None:
        r = ref(title="Mobilidade urbana e saúde coletiva", year=2020)
        res = pick_best(r, [make_paper("Química orgânica de polímeros", year=1998)])
        assert res.status is BibliographicStatus.NOT_FOUND
        assert res.best is None

    def test_no_candidates_is_not_found(self) -> None:
        assert pick_best(ref(title="Um título qualquer aqui"), []).status is (
            BibliographicStatus.NOT_FOUND
        )

    def test_unsearchable_reference_is_not_not_found(self) -> None:
        # "não procuramos" e "não existe" são estados diferentes, e confundi-los
        # faz o agente dizer que uma obra real não existe.
        res = pick_best(ref(title="ab"), [make_paper("qualquer")])
        assert res.status is BibliographicStatus.INSUFFICIENT_QUERY


class TestResolveByDOI:
    def test_doi_resolves_without_fuzzy(self) -> None:
        r = ref(doi="10.1/abc", title="Mobilidade urbana")
        res = resolve_by_doi(r, make_paper("Mobilidade urbana", doi="10.1/abc"))
        assert res.status is BibliographicStatus.VERIFIED
        assert res.score.method == "doi"
        assert res.score.total == 100.0

    def test_unresolvable_doi_is_not_found(self) -> None:
        assert resolve_by_doi(ref(doi="10.1/inexistente"), None).status is (
            BibliographicStatus.NOT_FOUND
        )

    def test_doi_pointing_to_a_different_title_is_flagged_by_low_title_score(self) -> None:
        # DOI copiado da referência errada: resolve, mas para outra obra.
        r = ref(doi="10.1/abc", title="Mobilidade urbana e saúde")
        res = resolve_by_doi(r, make_paper("Síntese de polímeros condutores", doi="10.1/abc"))
        assert res.status is BibliographicStatus.VERIFIED  # o DOI existe
        assert res.score.title < 60  # mas não é a obra citada


class TestParseReference:
    def test_extracts_doi_and_year(self) -> None:
        raw = ("SILVA, J. Mobilidade urbana no Brasil. Revista Brasileira de Transportes, "
               "2019. https://doi.org/10.1590/abc123")
        r = parse_reference(raw)
        assert r.doi == "10.1590/abc123"
        assert r.year == 2019
        assert "Silva" in r.authors

    def test_picks_a_plausible_title(self) -> None:
        raw = "SOUZA, M. Desigualdade educacional no ensino superior. Educação e Pesquisa, 2021."
        assert "Desigualdade educacional" in parse_reference(raw).title

    def test_garbage_is_unsearchable_not_wrongly_searched(self) -> None:
        assert not parse_reference("2019").is_searchable
        assert not parse_reference("").is_searchable
