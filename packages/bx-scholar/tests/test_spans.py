"""Localização de trechos — determinística e honesta sobre o que mede."""

from __future__ import annotations

from bx_scholar_core.clients.europepmc import parse_jats_sections

from bx_scholar.workflows.spans import find_spans, split_sentences, tokenize

JATS = """<article>
  <front><article-meta>
    <abstract><p>Avaliamos o efeito da arborizacao na temperatura urbana.</p></abstract>
  </article-meta></front>
  <body>
    <sec><title>Introduction</title>
      <p>Diversos autores relatam que a arborizacao reduz a temperatura. Este trabalho
      revisita a questao para o contexto brasileiro em cidades de porte medio.</p></sec>
    <sec><title>Methods</title>
      <p>Foram instaladas 42 estacoes meteorologicas em duas cidades ao longo de 18 meses,
      com medicao horaria de temperatura do ar a dois metros do solo.</p></sec>
    <sec><title>Results</title>
      <p>A arborizacao reduziu a temperatura media em 2,3 graus Celsius nas areas
      tratadas quando comparadas ao controle. O efeito foi maior no periodo vespertino.</p></sec>
  </body>
</article>"""

SECTIONS = parse_jats_sections(JATS)


class TestSentenceSplitting:
    def test_keeps_offsets(self) -> None:
        text = "Primeira frase aqui. Segunda frase aqui. Terceira frase."
        out = split_sentences(text)
        assert len(out) == 3
        for offset, sentence in out:
            # O offset é o que torna o trecho conferível no documento.
            assert text[offset : offset + len(sentence)].startswith(sentence[:20])

    def test_does_not_split_on_common_abbreviations(self) -> None:
        text = "Segundo Silva et al. a medida foi eficaz. Outro estudo discorda."
        assert len(split_sentences(text)) == 2


class TestTokenize:
    def test_drops_stopwords_and_accents(self) -> None:
        assert "temperatura" in tokenize("a temperatura da água")
        assert "de" not in tokenize("a temperatura da água")
        assert "agua" in tokenize("a temperatura da água")


class TestFindSpans:
    def test_jats_parsing_produced_sections(self) -> None:
        assert {s["section"] for s in SECTIONS} >= {"abstract", "introduction", "methods", "results"}

    def test_finds_the_relevant_section(self) -> None:
        spans = find_spans(SECTIONS, "quantas estacoes meteorologicas foram instaladas")
        assert spans
        assert spans[0].section == "methods"
        assert "42 estacoes" in spans[0].text

    def test_results_outrank_introduction_for_the_same_terms(self) -> None:
        # A mesma frase na introdução costuma estar resumindo o trabalho DOS
        # OUTROS; nos resultados é achado do próprio artigo.
        spans = find_spans(SECTIONS, "arborizacao reduziu temperatura")
        assert spans[0].section == "results"

    def test_reports_which_terms_matched(self) -> None:
        spans = find_spans(SECTIONS, "temperatura vespertino")
        assert spans
        assert set(spans[0].matched_terms) & {"temperatura", "vespertino"}

    def test_no_match_returns_empty_not_a_guess(self) -> None:
        # Lista vazia significa "não localizei" — quem chama NÃO pode ler isso
        # como "o documento contradiz a afirmação".
        assert find_spans(SECTIONS, "criptomoeda blockchain ethereum") == []

    def test_empty_question_returns_nothing(self) -> None:
        assert find_spans(SECTIONS, "") == []

    def test_respects_max_spans(self) -> None:
        spans = find_spans(SECTIONS, "temperatura arborizacao cidades estudo medicao", max_spans=2)
        assert len(spans) <= 2

    def test_spans_carry_location(self) -> None:
        span = find_spans(SECTIONS, "estacoes meteorologicas")[0]
        d = span.as_dict()
        assert d["location"]["char_start"] >= 0
        assert d["location"]["char_end"] > d["location"]["char_start"]
        assert d["section"] == "methods"

    def test_does_not_return_three_neighbours_of_the_same_passage(self) -> None:
        long_section = [
            {
                "section": "results",
                "title": "Results",
                "text": " ".join(
                    f"A medicao {i} indicou reducao de temperatura na area tratada." for i in range(8)
                ),
            }
        ]
        spans = find_spans(long_section, "reducao de temperatura na area tratada", max_spans=5)
        starts = [s.char_start for s in spans]
        assert all(abs(a - b) >= 200 for a in starts for b in starts if a != b)


class TestBilingualExpansion:
    """O caso comum do produto: pergunta em português, artigo em inglês."""

    EN_SECTIONS = [
        {
            "section": "methods",
            "title": "Methods",
            "text": "We used a generalized additive model with a sample size of 4,312 "
            "participants recruited across twelve cities. The statistical method "
            "accounted for seasonality.",
        },
        {
            "section": "results",
            "title": "Results",
            "text": "Greenness was associated with a reduction in heat-related mortality "
            "of 12% (95% confidence interval 8-16).",
        },
    ]

    def test_portuguese_question_finds_english_text(self) -> None:
        # Sem expansão isto devolvia ZERO trechos, medido contra um artigo real
        # do PMC — o documento tinha tudo, escrito em inglês.
        spans = find_spans(self.EN_SECTIONS, "qual metodo estatistico e o tamanho da amostra")
        assert spans
        assert spans[0].section == "methods"
        assert "sample size" in spans[0].text

    def test_portuguese_question_about_results(self) -> None:
        spans = find_spans(self.EN_SECTIONS, "qual foi a reducao da mortalidade")
        assert spans
        assert spans[0].section == "results"

    def test_english_question_still_works(self) -> None:
        spans = find_spans(self.EN_SECTIONS, "statistical method sample size")
        assert spans and spans[0].section == "methods"

    def test_expansion_keeps_originals_first(self) -> None:
        from bx_scholar.workflows.spans import expand_terms

        out = expand_terms(["metodo", "amostra"])
        assert out[:2] == ["metodo", "amostra"]
        assert "method" in out and "sample" in out

    def test_unknown_terms_are_untouched(self) -> None:
        from bx_scholar.workflows.spans import expand_terms

        assert expand_terms(["zzxq"]) == ["zzxq"]
