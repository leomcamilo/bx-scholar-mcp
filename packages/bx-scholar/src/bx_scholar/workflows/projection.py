"""Projeções — o que efetivamente vai para o prompt.

Esta é a fronteira que separa artefato de payload. O Evidence Pack pode ter
centenas de obras, trechos e metadados; o modelo recebe daqui um resumo com
**teto duro de caracteres**, e desce no pack com ``read_pack`` quando precisar.

Dois motivos concretos para o teto, não estética:

1. cada caractere aqui é token em **todo** turno seguinte da conversa;
2. do lado do BXat, ``bxat/sources_payload.py`` corta o payload agregado em
   600k chars — e o incidente que motivou aquele corte foi um array de 26,9 MB
   que matou uma aba do Chrome e produziu um JSON de chat de 56,9 MB.

O corte aqui é sempre **explícito**: quando algo é omitido, a projeção diz que
foi omitido e como recuperar. Truncar em silêncio é como se produz falsa
impressão de completude.
"""

from __future__ import annotations

import json
from typing import Any

# Abstract é o campo que mais cresce e o menos útil em lote — o modelo decide
# pelo título, ano, veículo e convergência de fontes; se quiser o resumo,
# chama read_pack ou retrieve_fulltext naquela obra.
_ABSTRACT_SNIPPET = 280


def _work_row(item_payload: dict, *, with_abstract: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "work_key": item_payload.get("work_key"),
        "title": item_payload.get("title"),
        "year": item_payload.get("year"),
        "venue": item_payload.get("venue"),
        "doi": item_payload.get("doi"),
        "cited_by": item_payload.get("cited_by"),
        # Convergência de fontes independentes: sinal de qualidade barato e
        # honesto, e o v1 jogava fora no merge do dedup.
        "sources": item_payload.get("source_count"),
        "integrity": item_payload.get("integrity_status"),
    }
    if item_payload.get("venue_tier"):
        row["venue_tier"] = item_payload["venue_tier"]
    if item_payload.get("is_open_access"):
        row["oa"] = True
    if with_abstract and item_payload.get("abstract"):
        text = item_payload["abstract"]
        row["abstract"] = (
            text if len(text) <= _ABSTRACT_SNIPPET else text[:_ABSTRACT_SNIPPET].rstrip() + "…"
        )
    return {k: v for k, v in row.items() if v is not None}


def _size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False))


def project_search(
    *,
    pack_id: str,
    mode: str,
    query: dict,
    works: list[dict],
    counts: dict,
    coverage: dict,
    limitations: list[str],
    max_chars: int,
    max_works: int,
) -> dict[str, Any]:
    """Monta a projeção de uma busca, cabendo no teto.

    Estratégia de encolhimento, nesta ordem: (1) cortar abstracts, (2) reduzir o
    número de obras. Nunca cortar ``coverage`` nem ``limitations`` — são eles
    que impedem o modelo de afirmar completude que não existe.
    """
    envelope: dict[str, Any] = {
        "pack_id": pack_id,
        "mode": mode,
        "query": query,
        "counts": counts,
        "coverage": coverage,
        "works": [],
        "limitations": list(limitations),
        "how_to_read_more": (
            f"read_pack(pack_id='{pack_id}', section='works', offset=N) para mais obras; "
            f"retrieve_fulltext(work='<doi ou work_key>') para trechos localizados."
        ),
    }

    candidates = works[:max_works]

    # Primeira tentativa: com abstract.
    rows = [_work_row(w, with_abstract=True) for w in candidates]
    envelope["works"] = rows
    if _size(envelope) <= max_chars:
        return envelope

    # Segunda: sem abstract.
    rows = [_work_row(w, with_abstract=False) for w in candidates]
    envelope["works"] = rows
    if _size(envelope) <= max_chars:
        envelope["limitations"].append(
            "Resumos omitidos da projeção por limite de tamanho — use read_pack para lê-los."
        )
        return envelope

    # Terceira: menos obras, sempre dizendo quantas ficaram de fora.
    while rows and _size(envelope) > max_chars:
        rows = rows[: max(1, int(len(rows) * 0.75))]
        envelope["works"] = rows
        if len(rows) == 1:
            break

    omitted = len(candidates) - len(rows)
    if omitted > 0:
        envelope["limitations"].append(
            f"{omitted} obra(s) omitida(s) desta projeção por limite de tamanho — "
            f"elas estão no pack e saem por read_pack(pack_id='{pack_id}', offset={len(rows)})."
        )
    return envelope


def project_pack_page(
    *,
    pack_id: str,
    section: str,
    rows: list[dict],
    offset: int,
    limit: int,
    total: int,
    max_chars: int,
) -> dict[str, Any]:
    """Projeção de uma página de ``read_pack``."""
    envelope: dict[str, Any] = {
        "pack_id": pack_id,
        "section": section,
        "offset": offset,
        "returned": len(rows),
        "total": total,
        "items": rows,
    }
    if offset + len(rows) < total:
        envelope["next_offset"] = offset + len(rows)

    while rows and _size(envelope) > max_chars:
        rows = rows[: max(1, len(rows) - 2)]
        envelope["items"] = rows
        envelope["returned"] = len(rows)
        envelope["next_offset"] = offset + len(rows)
        if len(rows) == 1:
            break
    return envelope
