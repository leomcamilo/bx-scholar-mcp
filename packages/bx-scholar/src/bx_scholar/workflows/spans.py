"""Localização de trechos — determinística, sem embedding e sem LLM.

O que este módulo faz e o que ele deliberadamente NÃO faz
---------------------------------------------------------

Ele encontra os trechos do documento que **contêm os termos** da pergunta e diz
onde eles estão. Ele não decide se o trecho *sustenta* uma afirmação — isso é
julgamento, sai do servidor com ``verdict="not_assessed"`` e cabe a quem chama,
registrado como ``assessor.type="caller"``.

Essa fronteira é o ponto central do desenho. Se o servidor devolvesse
"sustenta: 0.87" ao lado de "DOI existe: true", ele estaria empacotando um palpite
probabilístico com a mesma aparência de um fato computacional — que é o modo
elegante de lavar alucinação.

Por que BM25 lexical e não busca semântica: não precisa de GPU nem de modelo
(a VPS tem 245 Mi de RAM livre), é reprodutível — a mesma pergunta devolve os
mesmos trechos —, e é honesto sobre o que mede. "Este trecho contém seus termos"
é uma afirmação verificável; "este trecho é semanticamente próximo" já é opinião
de um modelo que ninguém auditou.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

# Palavras vazias em português e inglês. Lista curta de propósito: só o que
# aparece em quase toda frase e não discrimina nada.
_STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "em", "no",
    "na", "nos", "nas", "por", "para", "com", "sem", "que", "se", "e", "ou", "ao",
    "aos", "à", "às", "pelo", "pela", "entre", "sobre", "como", "mais", "foi",
    "the", "of", "and", "or", "to", "in", "on", "for", "with", "without", "that",
    "this", "these", "those", "is", "are", "was", "were", "be", "been", "at", "by",
    "as", "from", "it", "its", "we", "our",
}

_WORD_RE = re.compile(r"\w+", re.UNICODE)
# Corte de sentença: ponto final, interrogação ou exclamação seguidos de espaço
# e maiúscula. Abreviações comuns em texto acadêmico ficam protegidas.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý])")
_ABBREV = ("et al.", "e.g.", "i.e.", "cf.", "vs.", "Fig.", "Tab.", "Dr.", "p.")


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in normalized if not unicodedata.combining(c))


def tokenize(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(_fold(text)) if t not in _STOPWORDS and len(t) > 2]


# Glossário acadêmico PT↔EN. Existe por um motivo concreto, medido: o usuário
# brasileiro pergunta em português sobre um artigo em inglês. No primeiro teste
# real, "qual metodo estatistico foi usado e qual foi o tamanho da amostra"
# contra um artigo do PMC devolveu ZERO trechos — o documento tinha tudo isso,
# escrito como "statistical method" e "sample size".
#
# É expansão de termos, não tradução e muito menos busca semântica: uma tabela
# fixa, auditável, que não inventa relação nenhuma. Cobre o vocabulário de
# método e resultado, que é onde a verificação de uma afirmação acontece.
_GLOSSARY: dict[str, tuple[str, ...]] = {
    "metodo": ("method", "methods", "methodology"),
    "metodos": ("method", "methods"),
    "metodologia": ("methodology", "methods"),
    "amostra": ("sample", "cohort", "participants"),
    "amostragem": ("sampling",),
    "tamanho": ("size",),
    "resultado": ("result", "results", "finding", "findings"),
    "resultados": ("results", "findings"),
    "conclusao": ("conclusion", "conclusions"),
    "estatistico": ("statistical", "statistics"),
    "estatistica": ("statistical", "statistics"),
    "significancia": ("significance", "significant"),
    "correlacao": ("correlation", "correlated"),
    "regressao": ("regression",),
    "modelo": ("model", "modelling", "modeling"),
    "efeito": ("effect", "effects", "impact"),
    "impacto": ("impact", "effect"),
    "reducao": ("reduction", "reduced", "decrease"),
    "aumento": ("increase", "increased", "rise"),
    "risco": ("risk", "hazard"),
    "mortalidade": ("mortality", "deaths"),
    "morbidade": ("morbidity",),
    "prevalencia": ("prevalence",),
    "incidencia": ("incidence",),
    "participantes": ("participants", "subjects", "respondents"),
    "pacientes": ("patients",),
    "controle": ("control", "controls"),
    "grupo": ("group", "groups"),
    "ensaio": ("trial", "assay"),
    "coorte": ("cohort",),
    "revisao": ("review", "systematic"),
    "meta": ("meta", "metaanalysis"),
    "intervalo": ("interval",),
    "confianca": ("confidence",),
    "medida": ("measure", "measurement"),
    "medicao": ("measurement", "measured"),
    "dados": ("data", "dataset"),
    "variavel": ("variable", "variables"),
    "limitacao": ("limitation", "limitations"),
    "limitacoes": ("limitations",),
    "vies": ("bias",),
    "temperatura": ("temperature",),
    "urbano": ("urban",),
    "urbana": ("urban",),
    "cidade": ("city", "cities", "urban"),
    "cidades": ("cities", "city"),
    "saude": ("health",),
    "educacao": ("education", "educational"),
    "renda": ("income",),
    "desigualdade": ("inequality", "disparities", "inequities"),
    "politica": ("policy", "political"),
    "custo": ("cost", "costs"),
    "eficacia": ("efficacy", "effectiveness"),
    "seguranca": ("safety",),
    "qualidade": ("quality",),
    "poluicao": ("pollution",),
    "clima": ("climate",),
    "verde": ("green", "greenness"),
    "arborizacao": ("greenness", "tree", "canopy"),
    "mobilidade": ("mobility",),
    "transporte": ("transport", "transportation"),
}
# A tabela vale nos dois sentidos: o usuário também pergunta em inglês sobre
# artigo em português.
_GLOSSARY_REVERSE: dict[str, tuple[str, ...]] = {}
for _pt, _ens in _GLOSSARY.items():
    for _en in _ens:
        _GLOSSARY_REVERSE.setdefault(_en, ())
        _GLOSSARY_REVERSE[_en] = (*_GLOSSARY_REVERSE[_en], _pt)


def expand_terms(terms: list[str]) -> list[str]:
    """Acrescenta equivalentes conhecidos, preservando os originais."""
    out = list(terms)
    for term in terms:
        out.extend(_GLOSSARY.get(term, ()))
        out.extend(_GLOSSARY_REVERSE.get(term, ()))
    # dedup preservando ordem: os termos originais continuam primeiro
    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


@dataclass
class Span:
    """Um trecho localizável."""

    text: str
    section: str
    section_title: str
    ordinal: int
    char_start: int
    char_end: int
    score: float
    matched_terms: list[str]

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "section": self.section,
            "section_title": self.section_title,
            "location": {"char_start": self.char_start, "char_end": self.char_end},
            "score": round(self.score, 3),
            "matched_terms": self.matched_terms,
        }


def split_sentences(text: str) -> list[tuple[int, str]]:
    """Divide em sentenças preservando o offset de caractere.

    O offset é o que torna o trecho *localizável*: sem ele, o usuário recebe uma
    citação sem saber onde conferir no documento — e conferir é o ponto.
    """
    guarded = text
    for i, abbrev in enumerate(_ABBREV):
        guarded = guarded.replace(abbrev, f"\x00{i}\x00")

    out: list[tuple[int, str]] = []
    cursor = 0
    for piece in _SENTENCE_RE.split(guarded):
        restored = piece
        for i, abbrev in enumerate(_ABBREV):
            restored = restored.replace(f"\x00{i}\x00", abbrev)
        start = text.find(restored[:40], cursor) if restored[:40] else cursor
        if start < 0:
            start = cursor
        out.append((start, restored.strip()))
        cursor = start + len(restored)
    return [(s, t) for s, t in out if t]


def _bm25_scores(
    query_terms: list[str], docs: list[list[str]], *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """BM25 sobre as sentenças do próprio documento.

    Corpus local: o documento é o universo. Uma palavra que aparece em toda
    sentença do artigo (o tema dele) discrimina pouco *dentro* dele, e o IDF
    captura isso sem nenhuma configuração.
    """
    n = len(docs)
    if n == 0:
        return []
    avgdl = sum(len(d) for d in docs) / n or 1.0

    df = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1

    idf = {
        term: math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
        for term in set(query_terms)
    }

    scores: list[float] = []
    for doc in docs:
        tf = Counter(doc)
        dl = len(doc) or 1
        score = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if not f:
                continue
            score += idf[term] * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(score)
    return scores


#: Seções cuja evidência vale mais para sustentar uma afirmação empírica. Uma
#: frase idêntica na introdução costuma estar resumindo o trabalho DOS OUTROS.
_SECTION_WEIGHT = {
    "results": 1.25,
    "methods": 1.15,
    "conclusion": 1.10,
    "discussion": 1.05,
    "abstract": 1.0,
    "other": 0.95,
    "introduction": 0.85,
}


def find_spans(
    sections: list[dict],
    question: str,
    *,
    max_spans: int = 8,
    min_score: float = 0.1,
    context_sentences: int = 1,
) -> list[Span]:
    """Trechos mais aderentes aos termos da pergunta, com localização.

    Devolve lista vazia quando nada casa — resultado legítimo, e quem chama tem
    de tratá-lo como "não encontrei", nunca como "não sustenta".
    """
    base_terms = tokenize(question)
    if not base_terms or not sections:
        return []
    # Expansão bilíngue: o usuário pergunta em português sobre artigo em inglês.
    terms = expand_terms(base_terms)

    units: list[tuple[dict, int, int, str]] = []  # (section, ordinal, offset, texto)
    for section in sections:
        text = section.get("text") or ""
        for ordinal, (offset, sentence) in enumerate(split_sentences(text)):
            if len(sentence) < 40:  # fragmento não é evidência
                continue
            units.append((section, ordinal, offset, sentence))

    if not units:
        return []

    docs = [tokenize(u[3]) for u in units]
    scores = _bm25_scores(terms, docs)
    term_set = set(terms)

    ranked: list[Span] = []
    for (section, ordinal, offset, sentence), raw_score, doc in zip(units, scores, docs, strict=True):
        if raw_score <= min_score:
            continue
        weight = _SECTION_WEIGHT.get(section.get("section", "other"), 1.0)
        matched = sorted(term_set & set(doc))
        if not matched:
            continue

        # Contexto: uma sentença isolada frequentemente perde o sujeito ("Isso
        # reduziu o erro em 18%" — isso o quê?). Estender à vizinhança dá ao
        # leitor o mínimo para conferir sem devolver a seção inteira.
        section_text = section.get("text") or ""
        start = max(0, offset - context_sentences * 160)
        end = min(len(section_text), offset + len(sentence) + context_sentences * 160)
        snippet = section_text[start:end].strip()

        ranked.append(
            Span(
                text=snippet,
                section=section.get("section", "other"),
                section_title=section.get("title", ""),
                ordinal=ordinal,
                char_start=start,
                char_end=end,
                score=raw_score * weight,
                matched_terms=matched,
            )
        )

    ranked.sort(key=lambda s: -s.score)

    # Um trecho por região: sem isto, três sentenças vizinhas da mesma seção
    # ocupam o resultado inteiro repetindo o mesmo conteúdo.
    picked: list[Span] = []
    for span in ranked:
        if any(
            s.section_title == span.section_title and abs(s.char_start - span.char_start) < 200
            for s in picked
        ):
            continue
        picked.append(span)
        if len(picked) >= max_spans:
            break
    return picked
