"""``search_literature`` — a única porta de entrada de busca.

Substitui seis tools do v1 (``search_papers`` com ``sources=``,
``search_journal_papers``, e as buscas por fonte que existiam no monolito). O
agente decide **o que** procurar e **com que profundidade**; qual base consultar,
em que ordem, como deduplicar e como ranquear são detalhes internos.
"""

from __future__ import annotations

import json

from bx_scholar_core.logging import get_logger
from mcp.server.fastmcp import FastMCP

from bx_scholar.connectors.registry import MODE_SOURCES, SearchRequest, build_connectors
from bx_scholar.store import packs
from bx_scholar.workflows import integrity
from bx_scholar.workflows.fanout import fan_out
from bx_scholar.workflows.merge import merge_results, rank_works
from bx_scholar.workflows.projection import project_search

logger = get_logger(__name__)

_MODES = ("quick", "balanced", "deep")

DESCRIPTION = """Busca literatura acadêmica em várias bases ao mesmo tempo, deduplica, \
checa retratação e devolve um resumo com o identificador de um Evidence Pack persistido.

Modos:
- quick: uma base + cache, resposta em segundos. Use para orientação rápida.
- balanced (padrão): OpenAlex + CrossRef + SciELO em paralelo, com teto de tempo \
por base. Use para a maioria das perguntas.
- deep: todas as bases pertinentes, assíncrono — retorna o pack_id na hora e o \
resultado fica pronto depois; consulte com read_pack(section='job').

LEIA SEMPRE o bloco `coverage` da resposta antes de afirmar que algo "não existe \
na literatura": uma base em timeout_partial ou unavailable NÃO foi consultada com \
sucesso, e ausência de resultado ali não é ausência de literatura.

Obras retratadas ficam fora da seleção e marcadas no pack. Passe include_retracted=true \
apenas quando a pesquisa for SOBRE o artigo retratado."""


def register(server: FastMCP, settings, cache) -> None:
    @server.tool(name="search_literature", description=DESCRIPTION)
    async def search_literature(
        query: str,
        mode: str = "balanced",
        year_from: int | None = None,
        year_to: int | None = None,
        venue_issn: str | None = None,
        limit: int = 25,
        include_retracted: bool = False,
    ) -> str:
        query = (query or "").strip()
        if not query:
            # Erro de verdade sobe como exceção: o MCP marca isError e o modelo
            # sabe que falhou. O v1 devolvia {"error": ...} com status de
            # sucesso, e o modelo tinha que adivinhar lendo o JSON.
            raise ValueError("query é obrigatória")

        if mode not in _MODES:
            raise ValueError(f"mode deve ser um de {_MODES}, veio {mode!r}")

        if mode == "deep":
            # F5 entrega a execução assíncrona; até lá, deep degrada para
            # balanced de forma explícita em vez de fingir que rodou fundo.
            mode = "balanced"
            deep_note = [
                "Modo deep ainda não disponível nesta versão — a busca rodou como "
                "'balanced'. A cobertura é menor do que a de uma busca profunda."
            ]
        else:
            deep_note = []

        limit = max(1, min(int(limit), 100))
        req = SearchRequest(
            query=query,
            year_from=year_from,
            year_to=year_to,
            limit=limit,
            venue_issn=venue_issn,
        )

        pack_id = await packs.create_pack(
            kind="search",
            mode=mode,
            query={
                "query": query,
                "year_from": year_from,
                "year_to": year_to,
                "venue_issn": venue_issn,
                "limit": limit,
                "include_retracted": include_retracted,
            },
        )

        connectors = build_connectors(settings, cache, MODE_SOURCES[mode])
        try:
            fanout = await fan_out(connectors, req, timeout=settings.timeout_for(mode))
        finally:
            for c in connectors:
                await c.close()

        merged, duplicates = merge_results(fanout.results)
        ranked = rank_works(merged)

        selected, integrity_notes = await integrity.apply_gate(
            ranked, include_retracted=include_retracted
        )

        await packs.persist_works(ranked)
        await packs.add_work_items(pack_id, ranked, selected)

        # Deliberadamente SEM um total agregado. Somar os totais reportados por
        # cada base produz um número que parece "quantos artigos existem" e não
        # é: as bases se sobrepõem fortemente, então a soma superconta. Melhor
        # mostrar o que cada base disse e o que de fato foi consolidado.
        counts = {
            "reported_by_source": {
                r.name: r.reported_total for r in fanout.results if r.reported_total
            },
            "retrieved": sum(len(r.papers) for r in fanout.results),
            "works_merged": len(ranked),
            "works_selected": len(selected),
            "duplicates_removed": duplicates,
            "retracted_excluded": sum(
                1 for w in ranked if w.integrity_status == "retracted" and w.work_key not in selected
            ),
        }
        limitations = deep_note + fanout.limitations() + integrity_notes
        status = "partial" if fanout.degraded else "complete"

        await packs.finalize_pack(
            pack_id,
            counts=counts,
            coverage=fanout.coverage,
            limitations=limitations,
            status=status,
        )

        selected_rows = [
            {**_row_payload(w), "work_key": w.work_key}
            for w in ranked
            if w.work_key in selected
        ]

        projection = project_search(
            pack_id=pack_id,
            mode=mode,
            query={"q": query, "year_from": year_from, "year_to": year_to},
            works=selected_rows,
            counts=counts,
            coverage=fanout.coverage,
            limitations=limitations,
            max_chars=settings.projection_max_chars,
            max_works=min(limit, settings.projection_max_works),
        )

        logger.info(
            "search_literature_done",
            pack_id=pack_id,
            mode=mode,
            selected=len(selected),
            merged=len(ranked),
            status=status,
        )
        return json.dumps(projection, ensure_ascii=False)


def _row_payload(w) -> dict:
    p = w.paper
    return {
        "title": p.title,
        "year": p.year,
        "doi": p.doi or None,
        "venue": p.journal or p.venue or None,
        "cited_by": p.cited_by_count,
        "is_open_access": p.is_open_access,
        "abstract": p.abstract or None,
        "integrity_status": w.integrity_status,
        "source_count": w.source_count,
    }
