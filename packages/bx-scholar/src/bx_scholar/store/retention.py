"""Expurgo do store durável.

O cache HTTP tem ``sweep()``; o store não tinha nada — nenhuma linha era apagada
em lugar nenhum. Cada documento resolvido guarda o texto integral em
``fulltext_span`` (medido: 31k e 33k caracteres nos dois primeiros documentos),
e este banco divide a mesma instância Postgres com o BXat. Sem expurgo, a
trajetória é crescimento ilimitado num recurso compartilhado.

O que sai e o que fica
----------------------

**Sai**: ``pack`` e ``pack_item`` antigos, e os ``fulltext_doc``/``fulltext_span``
que ninguém mais referencia. São artefatos de uma pergunta específica; passado o
prazo, o valor deles é histórico e o custo é permanente.

**Fica**: ``work``, ``work_source``, ``venue`` e ``integrity``. O entity store é
o ativo — apagá-lo obrigaria a buscar de novo na rede o que já sabemos, e a
proveniência (``work_source``, com o timestamp de quando cada fonte viu a obra)
é justamente o registro que não dá para reconstruir depois.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from bx_scholar_core.logging import get_logger
from sqlalchemy import delete, func, select

from bx_scholar.store import db
from bx_scholar.store.models import FulltextDoc, FulltextSpan, Job, Pack, PackItem

logger = get_logger(__name__)

DEFAULT_RETENTION_DAYS = 90


@dataclass
class PruneReport:
    packs: int = 0
    pack_items: int = 0
    documents: int = 0
    spans: int = 0
    jobs: int = 0
    cutoff: int = 0

    def as_dict(self) -> dict:
        return {
            "cutoff_epoch": self.cutoff,
            "packs_removed": self.packs,
            "pack_items_removed": self.pack_items,
            "documents_removed": self.documents,
            "spans_removed": self.spans,
            "jobs_removed": self.jobs,
        }


async def prune(days: int = DEFAULT_RETENTION_DAYS, *, dry_run: bool = False) -> PruneReport:
    """Remove packs mais velhos que ``days`` e o texto que ficou órfão.

    Tudo numa transação: um expurgo que apaga os packs e falha antes de limpar
    os documentos deixaria texto órfão sem dono e sem prazo — pior que não ter
    expurgado.
    """
    if days < 1:
        raise ValueError("days deve ser >= 1 — expurgo com janela zero apagaria tudo")

    cutoff = int(time.time()) - days * 86_400
    report = PruneReport(cutoff=cutoff)

    async with db.session() as s:
        old_packs = (
            await s.execute(select(Pack.pack_id).where(Pack.created_at < cutoff))
        ).scalars().all()
        report.packs = len(old_packs)

        if old_packs:
            report.pack_items = int(
                (
                    await s.execute(
                        select(func.count())
                        .select_from(PackItem)
                        .where(PackItem.pack_id.in_(old_packs))
                    )
                ).scalar_one()
            )

        # Documentos órfãos: nenhum pack SOBREVIVENTE referencia a obra deles.
        # A checagem é contra os packs que ficam, não contra os que saem — senão
        # um documento citado por um pack recente seria apagado junto.
        surviving = select(PackItem.ref_key).join(Pack, Pack.pack_id == PackItem.pack_id).where(
            Pack.created_at >= cutoff
        )
        orphan_docs = (
            await s.execute(
                select(FulltextDoc.doc_id).where(FulltextDoc.work_key.notin_(surviving))
            )
        ).scalars().all()
        report.documents = len(orphan_docs)

        if orphan_docs:
            report.spans = int(
                (
                    await s.execute(
                        select(func.count())
                        .select_from(FulltextSpan)
                        .where(FulltextSpan.doc_id.in_(orphan_docs))
                    )
                ).scalar_one()
            )

        old_jobs = (
            await s.execute(select(Job.job_id).where(Job.created_at < cutoff))
        ).scalars().all()
        report.jobs = len(old_jobs)

        if dry_run:
            logger.info("prune_dry_run", **report.as_dict())
            return report

        # Ordem importa: filhos antes dos pais, por causa das chaves estrangeiras.
        if orphan_docs:
            await s.execute(delete(FulltextSpan).where(FulltextSpan.doc_id.in_(orphan_docs)))
            await s.execute(delete(FulltextDoc).where(FulltextDoc.doc_id.in_(orphan_docs)))
        if old_packs:
            await s.execute(delete(PackItem).where(PackItem.pack_id.in_(old_packs)))
            await s.execute(delete(Pack).where(Pack.pack_id.in_(old_packs)))
        if old_jobs:
            await s.execute(delete(Job).where(Job.job_id.in_(old_jobs)))

    logger.info("prune_done", days=days, **report.as_dict())
    return report


async def usage() -> dict:
    """Tamanho do store por tabela — para decidir se o prazo está bom."""
    async with db.session() as s:
        counts = {}
        for name, model in (
            ("pack", Pack), ("pack_item", PackItem), ("fulltext_doc", FulltextDoc),
            ("fulltext_span", FulltextSpan), ("job", Job),
        ):
            counts[name] = int(
                (await s.execute(select(func.count()).select_from(model))).scalar_one()
            )
        chars = (
            await s.execute(select(func.coalesce(func.sum(func.length(FulltextSpan.text)), 0)))
        ).scalar_one()
    return {"rows": counts, "fulltext_chars": int(chars)}
