"""Enriquecimento das obras selecionadas — etapa interna, nunca uma tool.

Passa pelo teste de promoção: o agente não tem razão legítima para decidir "e
agora avalie o periódico". Isso *sempre* acontece depois da seleção, então é
workflow. No v1 era ``rank_journal`` e ``get_journal_info``, duas das 21 tools.
"""

from __future__ import annotations

from bx_scholar_core.logging import get_logger

from bx_scholar.store import venues
from bx_scholar.workflows.merge import MergedWork

logger = get_logger(__name__)


async def attach_venue_assessment(works: list[MergedWork]) -> tuple[dict[str, dict], list[str]]:
    """Anexa avaliação de veículo às obras. Devolve (por work_key, notas).

    Um único lookup em lote para todas as obras — não uma consulta por obra.
    """
    if not works:
        return {}, []

    issns = [w.paper.issn for w in works if w.paper.issn]
    names = [w.paper.journal or w.paper.venue for w in works if (w.paper.journal or w.paper.venue)]

    if not await venues.count():
        return {}, [
            "Indicadores de veículo indisponíveis: a tabela de periódicos "
            "(SJR/Qualis/JQL) está vazia nesta instância. Nenhum estrato foi atribuído."
        ]

    found = await venues.lookup(issns, names)
    if not found:
        return {}, []

    from bx_scholar_core.ids import normalize_issn

    by_work: dict[str, dict] = {}
    fuzzy = 0
    for w in works:
        issn = normalize_issn(w.paper.issn)
        name = w.paper.journal or w.paper.venue or ""
        assessment = found.get(issn) or found.get(name)
        if assessment is None:
            continue
        by_work[w.work_key] = assessment.as_dict()
        if assessment.matched_by == "name":
            fuzzy += 1

    notes: list[str] = []
    if fuzzy:
        # Casamento por nome erra: "Revista Brasileira de Educação" e "Revista
        # Brasileira de Educação Especial" são periódicos diferentes com
        # estratos diferentes. Quem lê precisa saber que o vínculo é inferido.
        notes.append(
            f"{fuzzy} veículo(s) identificado(s) por semelhança de nome, não por ISSN — "
            f"o estrato atribuído pode ser de um periódico homônimo."
        )

    logger.info("venue_enrichment", works=len(works), matched=len(by_work), fuzzy=fuzzy)
    return by_work, notes
