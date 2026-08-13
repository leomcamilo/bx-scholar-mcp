"""Casamento bibliográfico com score real — camada determinística.

O v1 calculava um sinal de match e o jogava fora. Em ``crossref.py:126-133``:

    title_match = (query_title in best_title or best_title in query_title
                   or len(set(query_title.split()) & set(best_title.split())) > 2)
    return title_match, _parse_item(best)

...e o chamador (``tools/verify.py:39-48``) descartava esse booleano e devolvia
``confidence: "high"`` se o CrossRef respondeu, ``"medium"`` se foi o OpenAlex.
Uma **constante por branch**, não uma medida. Duas referências, uma exata e uma
com autor trocado e ano errado, saíam com a mesma "confiança alta".

Aqui o score é computado, devolvido e explicado por componente. E ele alimenta
``bibliographic_status``, que é um fato sobre o registro — não uma "confiança"
única que se confunde com "o artigo sustenta a afirmação".

**Isto não julga conteúdo.** Só responde: a obra citada existe, e é esta?
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from bx_scholar_core.ids import normalize_doi, normalize_title
from bx_scholar_core.models.paper import Paper
from rapidfuzz import fuzz


class BibliographicStatus(StrEnum):
    """Estado bibliográfico — determinístico, sobre o REGISTRO."""

    #: DOI resolvido, ou casamento forte e sem concorrente próximo.
    VERIFIED = "verified"
    #: Há candidatos, mas nenhum suficientemente bom.
    NOT_FOUND = "not_found"
    #: Dois ou mais candidatos empatados: não dá para afirmar qual é.
    AMBIGUOUS = "ambiguous"
    #: A referência veio incompleta demais para procurar.
    INSUFFICIENT_QUERY = "insufficient_query"


#: Acima disto, um candidato isolado é considerado a obra citada.
_STRONG = 85.0
#: Abaixo disto o candidato é ruído e nem entra na lista.
_FLOOR = 55.0
#: Se o segundo colocado chega a esta fração do primeiro, é ambíguo.
_AMBIGUITY_RATIO = 0.95

_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")
# Aceita "SILVA, J." (ABNT, caixa alta) e "Silva, J." (APA-ish).
_AUTHOR_RE = re.compile(r"([A-ZÀ-Ý][A-Za-zà-ÿÀ-Ý'-]{2,})\s*,\s*[A-ZÀ-Ý]\.")
_AUTHOR_LIST_RE = re.compile(r"^[A-ZÀ-Ý][A-Za-zà-ÿÀ-Ý'-]*\s*,\s*[A-ZÀ-Ý]")


@dataclass
class CitedReference:
    """O que o usuário afirma estar citando."""

    raw: str = ""
    doi: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None

    @property
    def is_searchable(self) -> bool:
        """Há material suficiente para procurar?

        Só o ano, ou só um sobrenome, não é referência — é palpite. Devolver
        ``not_found`` nesse caso mentiria: não procuramos, faltou pergunta.
        """
        return bool(normalize_doi(self.doi)) or len(normalize_title(self.title).split()) >= 3

    def as_dict(self) -> dict:
        out = {k: v for k, v in {
            "raw": self.raw or None,
            "doi": normalize_doi(self.doi) or None,
            "title": self.title or None,
            "authors": self.authors or None,
            "year": self.year,
        }.items() if v}
        return out


@dataclass
class MatchScore:
    """Score decomposto — cada parcela auditável separadamente."""

    total: float
    title: float
    year: float
    author: float
    method: str  # doi | bibliographic

    def as_dict(self) -> dict:
        return {
            "total": round(self.total, 1),
            "components": {
                "title": round(self.title, 1),
                "year": round(self.year, 1),
                "author": round(self.author, 1),
            },
            "method": self.method,
        }


def _year_component(ref: CitedReference, candidate: Paper) -> float:
    """Ano casa? Tolerância de 1 ano é deliberada.

    Online-first, ahead-of-print e a diferença entre ano de aceite e de fascículo
    produzem discrepância de um ano em referência legítima o tempo todo. Dois
    anos ou mais já é outro trabalho, ou erro de quem citou.
    """
    if not ref.year or not candidate.year:
        return 50.0  # ausência não é evidência contra
    delta = abs(ref.year - candidate.year)
    if delta == 0:
        return 100.0
    if delta == 1:
        return 75.0
    if delta <= 3:
        return 25.0
    return 0.0


def _author_component(ref: CitedReference, candidate: Paper) -> float:
    """Algum sobrenome citado aparece na autoria do candidato?

    Compara sobrenomes, não nomes completos: a referência traz "Silva, J." e o
    registro traz "João Pedro da Silva". Comparar a string inteira produziria
    zero para um casamento perfeito.
    """
    if not ref.authors or not candidate.authors:
        return 50.0
    cited = {normalize_title(a).split()[-1] for a in ref.authors if normalize_title(a)}
    found = {normalize_title(a.name).split()[-1] for a in candidate.authors if normalize_title(a.name)}
    if not cited or not found:
        return 50.0
    return 100.0 if cited & found else 10.0


def score_candidate(ref: CitedReference, candidate: Paper) -> MatchScore:
    """Compara referência e candidato. Título domina; ano e autoria ajustam."""
    ref_title = normalize_title(ref.title)
    cand_title = normalize_title(candidate.title)

    if ref_title and cand_title:
        # token_set_ratio tolera ordem e subtítulo faltando — comum em
        # referência abreviada.
        title = max(fuzz.token_set_ratio(ref_title, cand_title), fuzz.ratio(ref_title, cand_title))
    else:
        title = 0.0

    year = _year_component(ref, candidate)
    author = _author_component(ref, candidate)

    # Pesos: o título é o que identifica a obra; ano e autoria confirmam ou
    # levantam suspeita, mas sozinhos não identificam nada.
    total = 0.70 * title + 0.15 * year + 0.15 * author
    return MatchScore(total=total, title=title, year=year, author=author, method="bibliographic")


@dataclass
class MatchResult:
    status: BibliographicStatus
    best: Paper | None = None
    score: MatchScore | None = None
    runner_up: Paper | None = None
    runner_up_score: float | None = None
    candidates_considered: int = 0

    def as_dict(self) -> dict:
        out: dict = {
            "status": str(self.status),
            "candidates_considered": self.candidates_considered,
        }
        if self.best is not None:
            out["match"] = {
                "title": self.best.title,
                "year": self.best.year,
                "doi": self.best.doi or None,
                "venue": self.best.journal or None,
                "authors": [a.name for a in (self.best.authors or [])][:6],
            }
        if self.score is not None:
            out["match_score"] = self.score.as_dict()
        if self.runner_up is not None:
            out["runner_up"] = {
                "title": self.runner_up.title,
                "year": self.runner_up.year,
                "doi": self.runner_up.doi or None,
                "score": round(self.runner_up_score or 0.0, 1),
            }
        return out


def resolve_by_doi(ref: CitedReference, fetched: Paper | None) -> MatchResult:
    """DOI é identificador: resolveu, é aquele. Sem score fuzzy.

    A única checagem que resta é de coerência — um DOI que resolve para um
    título completamente diferente do citado costuma ser DOI copiado errado, e
    quem lê precisa saber disso.
    """
    if fetched is None:
        return MatchResult(status=BibliographicStatus.NOT_FOUND, candidates_considered=0)

    title = 100.0
    if normalize_title(ref.title) and normalize_title(fetched.title):
        title = float(fuzz.token_set_ratio(normalize_title(ref.title), normalize_title(fetched.title)))

    return MatchResult(
        status=BibliographicStatus.VERIFIED,
        best=fetched,
        score=MatchScore(total=100.0, title=title, year=_year_component(ref, fetched),
                         author=_author_component(ref, fetched), method="doi"),
        candidates_considered=1,
    )


def pick_best(ref: CitedReference, candidates: list[Paper]) -> MatchResult:
    """Escolhe entre candidatos de uma busca bibliográfica.

    Empate técnico vira ``ambiguous``, não "o primeiro". Devolver o primeiro de
    dois candidatos indistinguíveis é como se atribui a citação ao artigo
    errado com aparência de certeza.
    """
    if not ref.is_searchable:
        return MatchResult(status=BibliographicStatus.INSUFFICIENT_QUERY)
    if not candidates:
        return MatchResult(status=BibliographicStatus.NOT_FOUND)

    scored = sorted(
        ((score_candidate(ref, c), c) for c in candidates),
        key=lambda pair: -pair[0].total,
    )
    best_score, best = scored[0]

    if best_score.total < _FLOOR:
        return MatchResult(
            status=BibliographicStatus.NOT_FOUND, candidates_considered=len(candidates)
        )

    runner_up = runner_up_score = None
    if len(scored) > 1:
        runner_up_score, runner_up = scored[1][0].total, scored[1][1]
        if runner_up_score >= best_score.total * _AMBIGUITY_RATIO:
            return MatchResult(
                status=BibliographicStatus.AMBIGUOUS,
                best=best,
                score=best_score,
                runner_up=runner_up,
                runner_up_score=runner_up_score,
                candidates_considered=len(candidates),
            )

    status = (
        BibliographicStatus.VERIFIED
        if best_score.total >= _STRONG
        else BibliographicStatus.NOT_FOUND
    )
    return MatchResult(
        status=status,
        best=best if status is BibliographicStatus.VERIFIED else None,
        score=best_score if status is BibliographicStatus.VERIFIED else None,
        runner_up=runner_up,
        runner_up_score=runner_up_score,
        candidates_considered=len(candidates),
    )


def parse_reference(raw: str) -> CitedReference:
    """Extrai o que der de uma referência solta em texto.

    Não é parser de ABNT nem de APA — é extração best-effort de DOI, ano e
    título provável, suficiente para procurar. O que não der para extrair vira
    ``insufficient_query``, que é honesto, em vez de uma busca ruim que devolve
    ``not_found`` e passa a impressão de que a obra não existe.
    """
    raw = (raw or "").strip()
    ref = CitedReference(raw=raw)

    if m := re.search(r"10\.\d{4,9}/\S+", raw):
        ref.doi = normalize_doi(m.group(0))

    years = _YEAR_RE.findall(raw)
    if years:
        ref.year = int(years[0])

    # Heurística de título: o PRIMEIRO trecho substantivo que não é lista de
    # autores nem metadado. Não o mais longo — o mais longo costuma ser o nome
    # do periódico com volume, número e páginas, e ABNT e APA colocam o título
    # logo depois da autoria.
    parts = [p.strip() for p in re.split(r"\.\s+", raw) if len(p.strip()) > 12]
    candidates = [
        p
        for p in parts
        if not _AUTHOR_LIST_RE.match(p)
        and "doi" not in p.lower()
        and not p.lower().startswith(("disponível em", "available at", "http"))
    ]
    if candidates or parts:
        ref.title = (candidates or parts)[0][:300]

    # Autores. O regex aceita SOBRENOME em caixa alta: ABNT manda caixa alta na
    # autoria ("SILVA, J."), e a versão anterior — que exigia minúscula depois
    # da inicial — não reconhecia NENHUM autor no estilo dominante no Brasil.
    ref.authors = [
        s.capitalize() if s.isupper() else s
        for s in _AUTHOR_RE.findall(raw[:200])
    ]
    return ref
