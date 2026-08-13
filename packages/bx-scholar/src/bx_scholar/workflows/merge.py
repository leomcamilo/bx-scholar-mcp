"""Merge e deduplicação com identidade única e proveniência preservada.

Duas correções sobre o v1:

1. **Uma identidade só.** ``dedup.py`` usava DOI → título+ano; ``snowball.py:43``
   usava DOI → OpenAlex → título minúsculo. Registros que uma função considerava
   a mesma obra, a outra considerava duas. Aqui existe ``ids.work_key`` e ponto.

2. **Proveniência sobrevive ao merge.** ``dedup.py:42-57`` ficava com o registro
   de maior ``_metadata_score`` e **descartava o outro** — junto com o
   ``source_api`` dele. O resultado dizia "veio do OpenAlex" para uma obra que
   três fontes independentes confirmaram. Convergência de fontes independentes é
   sinal de qualidade; jogá-la fora era perder informação de graça.
   Agora o merge acumula ``seen_in`` e enriquece campo a campo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from bx_scholar_core.ids import normalize_doi, normalize_issn, normalize_title, work_key
from bx_scholar_core.models.paper import Paper
from rapidfuzz import fuzz

from bx_scholar.workflows.fanout import SourceResult

# Limiar do casamento por título quando não há identificador. Alto de propósito:
# um falso positivo aqui funde duas obras distintas e o erro fica invisível.
_TITLE_MATCH_THRESHOLD = 92


@dataclass
class MergedWork:
    """Obra consolidada, com o rastro de quem a viu."""

    work_key: str
    paper: Paper
    seen_in: dict[str, int] = field(default_factory=dict)  # fonte -> epoch
    integrity_status: str = "unknown"

    @property
    def source_count(self) -> int:
        return len(self.seen_in)


def _completeness(p: Paper) -> int:
    """Quão completo é o registro — usado para escolher o valor base."""
    score = 0
    if p.doi:
        score += 3
    if p.abstract:
        score += 2
    if p.authors:
        score += 1
    if p.cited_by_count:
        score += 1
    if p.journal:
        score += 1
    if p.year:
        score += 1
    return score


def _enrich(base: Paper, other: Paper) -> Paper:
    """Preenche buracos de ``base`` com o que ``other`` tem.

    Enriquecer em vez de escolher: o CrossRef costuma ter o ISSN que o OpenAlex
    não trouxe, o OpenAlex costuma ter o abstract que o CrossRef não tem. Ficar
    só com "o mais completo" desperdiça as duas metades.
    """
    for attr in (
        "doi", "abstract", "journal", "issn", "venue", "openalex_id", "s2_id",
        "arxiv_id", "pdf_url", "tldr", "year",
    ):
        if not getattr(base, attr, None) and getattr(other, attr, None):
            setattr(base, attr, getattr(other, attr))

    if not base.authors and other.authors:
        base.authors = other.authors
    # Contagem de citação: fica a maior. Bases têm janelas de atualização
    # diferentes, e a menor é quase sempre a mais desatualizada.
    if (other.cited_by_count or 0) > (base.cited_by_count or 0):
        base.cited_by_count = other.cited_by_count
    if other.is_open_access and not base.is_open_access:
        base.is_open_access = True
    return base


def _key_for(paper: Paper) -> str:
    return work_key(
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        openalex_id=paper.openalex_id,
        s2_id=paper.s2_id,
        title=paper.title,
        year=paper.year,
    )


def merge_results(results: list[SourceResult]) -> tuple[list[MergedWork], int]:
    """Funde os resultados de todas as fontes. Devolve (obras, duplicatas removidas).

    Dois passes: primeiro por chave canônica (barato e exato), depois casamento
    por título só entre as obras que ficaram sem identificador — que é onde o
    O(n²) do v1 realmente doía, mas agora sobre um conjunto muito menor.
    """
    by_key: dict[str, MergedWork] = {}
    keyless: list[tuple[Paper, str, int]] = []
    raw_count = 0
    ts = int(time.time())  # momento da recuperação — a proveniência que o v1 não tinha

    for res in results:
        for paper in res.papers:
            raw_count += 1
            # Normaliza na entrada: o resto do sistema pode confiar nos campos.
            paper.doi = normalize_doi(paper.doi)
            paper.issn = normalize_issn(paper.issn) or paper.issn
            key = _key_for(paper)

            if not key:
                continue
            if key.startswith("title:"):
                keyless.append((paper, res.name, ts))
                continue

            if key in by_key:
                existing = by_key[key]
                if _completeness(paper) > _completeness(existing.paper):
                    existing.paper = _enrich(paper, existing.paper)
                else:
                    existing.paper = _enrich(existing.paper, paper)
                existing.seen_in.setdefault(res.name, ts)
            else:
                by_key[key] = MergedWork(work_key=key, paper=paper, seen_in={res.name: ts})

    # Segundo passe: obras sem identificador, casadas por título + ano.
    for paper, source, ts in keyless:
        title = normalize_title(paper.title)
        if not title:
            continue
        matched: MergedWork | None = None
        for candidate in by_key.values():
            if paper.year and candidate.paper.year and paper.year != candidate.paper.year:
                continue
            if fuzz.ratio(title, normalize_title(candidate.paper.title)) >= _TITLE_MATCH_THRESHOLD:
                matched = candidate
                break
        if matched is not None:
            matched.paper = _enrich(matched.paper, paper)
            matched.seen_in.setdefault(source, ts)
        else:
            key = _key_for(paper)
            by_key[key] = MergedWork(work_key=key, paper=paper, seen_in={source: ts})

    merged = list(by_key.values())
    return merged, raw_count - len(merged)


def rank_works(works: list[MergedWork], *, year_from: int | None = None) -> list[MergedWork]:
    """Ordena por um sinal composto simples e explicável.

    Deliberadamente **não** é um score opaco: convergência de fontes
    independentes primeiro, citações depois, recência por último. Qualquer
    ordenação aqui é heurística de apresentação, não julgamento de qualidade —
    e a projeção deixa isso claro para o modelo.
    """
    def sort_key(w: MergedWork) -> tuple:
        return (
            -w.source_count,
            -(w.paper.cited_by_count or 0),
            -(w.paper.year or 0),
        )

    return sorted(works, key=sort_key)
