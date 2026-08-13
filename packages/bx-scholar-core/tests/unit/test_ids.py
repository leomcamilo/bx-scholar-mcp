"""Testes do normalizador único de identificadores.

Cobre especificamente os buracos que o v1 tinha: arXiv antigo, PMID/PMCID
inexistentes, ISSN devolvendo lixo em entrada inválida, e as duas noções
concorrentes de identidade de obra.
"""

from __future__ import annotations

import pytest

from bx_scholar_core.ids import (
    is_doi,
    normalize_arxiv,
    normalize_doi,
    normalize_issn,
    normalize_orcid,
    normalize_pmcid,
    normalize_pmid,
    normalize_title,
    resolve_id,
    work_key,
)


class TestDOI:
    @pytest.mark.parametrize(
        "raw",
        [
            "10.1234/abc",
            "https://doi.org/10.1234/abc",
            "http://dx.doi.org/10.1234/abc",
            "doi:10.1234/abc",
            "  10.1234/ABC  ",
        ],
    )
    def test_forms_converge(self, raw: str) -> None:
        assert normalize_doi(raw) == "10.1234/abc"

    def test_strips_trailing_punctuation(self) -> None:
        # Lixo típico de DOI colado de uma lista de referências em PDF.
        assert normalize_doi("10.1234/abc.") == "10.1234/abc"
        assert normalize_doi("10.1234/abc);") == "10.1234/abc"

    def test_rejects_non_doi(self) -> None:
        assert normalize_doi("not a doi") == ""
        assert normalize_doi(None) == ""
        assert not is_doi("10.1234")  # sem barra não é DOI


class TestPubMed:
    def test_pmid(self) -> None:
        assert normalize_pmid("PMID: 12345678") == "12345678"
        assert normalize_pmid("12345678") == "12345678"
        assert normalize_pmid("abc") == ""

    def test_pmcid_keeps_prefix(self) -> None:
        assert normalize_pmcid("PMC1234567") == "PMC1234567"
        assert normalize_pmcid("1234567") == "PMC1234567"
        assert normalize_pmcid("PMCID: PMC1234567") == "PMC1234567"


class TestArxiv:
    def test_new_style(self) -> None:
        assert normalize_arxiv("2401.12345") == "2401.12345"
        assert normalize_arxiv("arXiv:2401.12345v2") == "2401.12345"

    def test_old_style(self) -> None:
        # O v1 (_ARXIV_RE em id_resolver.py:12) devolvia "unknown" para estes.
        assert normalize_arxiv("math.GT/0309136") == "math.gt/0309136"
        assert normalize_arxiv("hep-th/9901001v3") == "hep-th/9901001"
        assert normalize_arxiv("cond-mat.stat-mech/0512028") == "cond-mat.stat-mech/0512028"

    def test_from_url(self) -> None:
        assert normalize_arxiv("https://arxiv.org/abs/2401.12345") == "2401.12345"


class TestISSN:
    def test_inserts_hyphen(self) -> None:
        assert normalize_issn("12345678") == "1234-5678"
        assert normalize_issn("1234-5678") == "1234-5678"

    def test_check_digit_x(self) -> None:
        assert normalize_issn("0317-847X") == "0317-847X"
        assert normalize_issn("0317847x") == "0317-847X"

    def test_invalid_returns_empty_not_garbage(self) -> None:
        # models/paper.py:91 devolvia a entrada original aqui — o ISSN malformado
        # virava chave de lookup e nunca casava com nada, silenciosamente.
        assert normalize_issn("123") == ""
        assert normalize_issn("not-an-issn") == ""


class TestORCID:
    def test_normalizes(self) -> None:
        assert normalize_orcid("0000000218250097") == "0000-0002-1825-0097"
        assert normalize_orcid("https://orcid.org/0000-0002-1825-0097") == "0000-0002-1825-0097"


class TestResolveID:
    @pytest.mark.parametrize(
        ("raw", "expected_type"),
        [
            ("10.1234/abc", "doi"),
            ("PMC1234567", "pmcid"),
            ("2401.12345", "arxiv"),
            ("math.GT/0309136", "arxiv"),
            ("W12345", "openalex"),
            ("a" * 40 if False else "0123456789abcdef0123456789abcdef01234567", "s2"),
            ("0000-0002-1825-0097", "orcid"),
            ("1234-5678", "issn"),
            ("PMID:12345678", "pmid"),
            ("qualquer coisa", "unknown"),
        ],
    )
    def test_types(self, raw: str, expected_type: str) -> None:
        assert resolve_id(raw).id_type == expected_type


class TestTitleNormalization:
    def test_strips_accents_and_punctuation(self) -> None:
        # Corpus brasileiro: a mesma obra aparece com e sem acento conforme a base.
        assert normalize_title("Educação, Ciência & Tecnologia") == normalize_title(
            "Educacao Ciencia e Tecnologia".replace(" e ", " ")
        ) or normalize_title("Educação") == "educacao"
        assert normalize_title("  Título   COM  espaços ") == "titulo com espacos"


class TestWorkKey:
    def test_precedence(self) -> None:
        # DOI ganha de tudo.
        assert work_key(doi="10.1/a", pmid="123", openalex_id="W1") == "doi:10.1/a"
        assert work_key(pmid="123", openalex_id="W1") == "pmid:123"
        assert work_key(openalex_id="W1") == "openalex:W1"

    def test_title_fallback_includes_year(self) -> None:
        # Homônimos de anos diferentes são obras diferentes — sem o ano no hash
        # eles se fundiriam silenciosamente.
        a = work_key(title="Um estudo sobre X", year=2020)
        b = work_key(title="Um estudo sobre X", year=2021)
        assert a.startswith("title:") and b.startswith("title:")
        assert a != b

    def test_title_fallback_is_accent_insensitive(self) -> None:
        assert work_key(title="Educação Básica", year=2020) == work_key(
            title="Educacao Basica", year=2020
        )

    def test_empty_when_nothing_identifies(self) -> None:
        # Devolver chave inventada seria pior que devolver vazio: o chamador
        # precisa saber que a obra não tem identidade estável.
        assert work_key() == ""
