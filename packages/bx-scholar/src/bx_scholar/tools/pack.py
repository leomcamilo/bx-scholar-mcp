"""``read_pack`` — descer num Evidence Pack já produzido.

Passa a regra de promoção acordada ("o agente tem razão legítima para decidir se
e quando isso acontece?"): decidir se vale aprofundar numa busca, ou responder
com o que já tem, é decisão do modelo — depende da pergunta do usuário e do que
a projeção mostrou. Paginar, montar o pack e ordenar não são.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from bx_scholar.store import packs
from bx_scholar.workflows.projection import project_pack_page

_SECTIONS = ("summary", "works", "excluded", "coverage", "claims", "job")

DESCRIPTION = """Lê partes de um Evidence Pack produzido por search_literature ou \
verify_claims. Use quando a projeção inicial não bastou.

Seções:
- summary: contagens, cobertura por base e limitações do pack.
- works: obras selecionadas, paginadas (use offset para avançar).
- excluded: obras que ficaram FORA da seleção e por quê (retratadas, por exemplo).
- coverage: só o estado por base — quais foram consultadas com sucesso.
- claims: afirmações verificadas e seus três estados (packs de verify_claims).
- job: AINDA VAZIO — depende do modo deep, que não existe nesta versão.

Não invente pack_id: use o que veio na resposta anterior."""


def register(server: FastMCP, settings) -> None:
    @server.tool(name="read_pack", description=DESCRIPTION)
    async def read_pack(
        pack_id: str,
        section: str = "works",
        offset: int = 0,
        limit: int = 25,
        work_key: str | None = None,
    ) -> str:
        if section not in _SECTIONS:
            raise ValueError(f"section deve ser um de {_SECTIONS}, veio {section!r}")

        pack = await packs.get_pack(pack_id)
        if pack is None:
            raise ValueError(
                f"pack {pack_id!r} não existe. Use o pack_id devolvido pela busca anterior."
            )

        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 100))

        if section == "summary":
            return json.dumps(
                {
                    "pack_id": pack.pack_id,
                    "kind": pack.kind,
                    "mode": pack.mode,
                    "status": pack.status,
                    "query": pack.query,
                    "counts": pack.counts,
                    "coverage": pack.coverage,
                    "limitations": pack.limitations,
                    "workflow_version": pack.workflow_version,
                    "created_at": pack.created_at,
                },
                ensure_ascii=False,
            )

        if section == "coverage":
            return json.dumps(
                {
                    "pack_id": pack.pack_id,
                    "coverage": pack.coverage,
                    "limitations": pack.limitations,
                    "status": pack.status,
                    "reading": (
                        "complete/partial = base respondeu; timeout_partial, unavailable, "
                        "rate_limited e error = a base NÃO foi coberta nesta busca."
                    ),
                },
                ensure_ascii=False,
            )

        if section == "job":
            from bx_scholar.store.jobs import job_for_pack

            job = await job_for_pack(pack_id)
            if job is None:
                return json.dumps(
                    {"pack_id": pack_id, "job": None, "pack_status": pack.status},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "pack_id": pack_id,
                    "job_id": job.job_id,
                    "state": job.state,
                    "progress": job.progress,
                    "error": job.error,
                },
                ensure_ascii=False,
            )

        # Detalhe de uma obra específica.
        if work_key:
            item = await packs.get_item(pack_id, work_key)
            if item is None:
                raise ValueError(f"obra {work_key!r} não está no pack {pack_id!r}")
            return json.dumps(
                {"pack_id": pack_id, "work_key": work_key, "selected": item.selected,
                 **item.payload},
                ensure_ascii=False,
            )

        item_type = "claim" if section == "claims" else "work"
        # None = todas; True = só selecionadas; False = só excluídas. O filtro
        # vai para o WHERE, então offset, limit e total valem sobre o conjunto
        # certo.
        wanted = {"works": True, "excluded": False}.get(section)
        rows, total = await packs.get_items(
            pack_id, item_type=item_type, offset=offset, limit=limit, selected=wanted
        )

        if section == "claims":
            # O payload do claim já é o relatório completo; não achatar nem
            # renomear a chave, senão os três estados se misturam.
            payload = [{"rank": r.rank, **r.payload} for r in rows]
        else:
            payload = [
                {"work_key": r.ref_key, "rank": r.rank, "selected": r.selected, **r.payload}
                for r in rows
            ]

        return json.dumps(
            project_pack_page(
                pack_id=pack_id,
                section=section,
                rows=payload,
                offset=offset,
                limit=limit,
                total=total,
                max_chars=settings.projection_max_chars,
            ),
            ensure_ascii=False,
        )
