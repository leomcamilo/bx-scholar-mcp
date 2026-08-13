"""Teto de projeção — o corte é sempre explícito.

Truncar em silêncio é como se produz falsa impressão de completude, que é o
pior modo de falha de uma ferramenta de pesquisa.
"""

from __future__ import annotations

import json

from bx_scholar.workflows.projection import project_search


def works(n: int, *, abstract_len: int = 0) -> list[dict]:
    return [
        {
            "work_key": f"doi:10.1/{i}",
            "title": f"Um estudo bastante longo sobre o tema numero {i}",
            "year": 2020 + (i % 5),
            "venue": "Revista Brasileira de Alguma Coisa",
            "doi": f"10.1/{i}",
            "cited_by": i,
            "source_count": 2,
            "integrity_status": "clear",
            "abstract": "x" * abstract_len if abstract_len else None,
        }
        for i in range(n)
    ]


def size(p: dict) -> int:
    return len(json.dumps(p, ensure_ascii=False))


BASE = dict(
    pack_id="pk_test",
    mode="balanced",
    query={"q": "teste"},
    counts={"works_found": 500},
    coverage={"openalex": "complete", "scielo": "timeout_partial"},
    limitations=["scielo: excedeu o tempo limite"],
)


class TestCeiling:
    def test_fits_under_ceiling(self) -> None:
        p = project_search(**BASE, works=works(10), max_chars=12000, max_works=25)
        assert size(p) <= 12000
        assert len(p["works"]) == 10

    def test_drops_abstracts_first(self) -> None:
        p = project_search(
            **BASE, works=works(20, abstract_len=2000), max_chars=6000, max_works=25
        )
        assert size(p) <= 6000
        assert all("abstract" not in w for w in p["works"])
        assert any("Resumos omitidos" in n for n in p["limitations"])

    def test_then_drops_works_and_says_so(self) -> None:
        p = project_search(**BASE, works=works(200), max_chars=3000, max_works=200)
        assert size(p) <= 3000
        assert len(p["works"]) < 200
        assert any("omitida" in n for n in p["limitations"])
        # E diz como recuperar o resto.
        assert any("read_pack" in n for n in p["limitations"])

    def test_coverage_and_limitations_are_never_cut(self) -> None:
        # São eles que impedem o modelo de afirmar completude que não existe —
        # se algo tem de sair, sai obra, nunca a cobertura.
        p = project_search(**BASE, works=works(400), max_chars=1200, max_works=400)
        assert p["coverage"] == BASE["coverage"]
        assert "scielo: excedeu o tempo limite" in p["limitations"]

    def test_max_works_is_respected_even_with_room_to_spare(self) -> None:
        p = project_search(**BASE, works=works(100), max_chars=999_999, max_works=25)
        assert len(p["works"]) == 25

    def test_tells_the_model_how_to_go_deeper(self) -> None:
        p = project_search(**BASE, works=works(5), max_chars=12000, max_works=25)
        assert "read_pack" in p["how_to_read_more"]
        assert "retrieve_fulltext" in p["how_to_read_more"]
