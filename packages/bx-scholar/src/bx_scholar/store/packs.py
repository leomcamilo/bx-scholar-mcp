"""Persistência de Evidence Packs.

O pack é a unidade durável: versionada, navegável e exportável. O modelo nunca
o recebe inteiro — recebe a projeção de ``workflows/projection.py`` e usa
``read_pack`` para descer nele. Essa separação é o que impede que "doze campos
de evidência" virem bomba de tokens no prompt.
"""

from __future__ import annotations

import secrets
import time

from sqlalchemy import func, select

from bx_scholar import WORKFLOW_VERSION
from bx_scholar.store import db
from bx_scholar.store.models import Pack, PackItem, Work, WorkSource
from bx_scholar.workflows.merge import MergedWork


def new_pack_id() -> str:
    return f"pk_{secrets.token_hex(8)}"


async def create_pack(
    *,
    kind: str,
    mode: str | None,
    query: dict,
    status: str = "building",
) -> str:
    pack_id = new_pack_id()
    now = int(time.time())
    async with db.session() as s:
        s.add(
            Pack(
                pack_id=pack_id,
                kind=kind,
                mode=mode,
                status=status,
                query=query,
                counts={},
                coverage={},
                limitations=[],
                provenance={"generated_by": WORKFLOW_VERSION, "generated_at": now},
                workflow_version=WORKFLOW_VERSION,
                created_at=now,
                updated_at=now,
            )
        )
    return pack_id


async def persist_works(works: list[MergedWork]) -> None:
    """Upsert no entity store, com uma linha de proveniência por fonte.

    Escrita idempotente por ``work_key``: rodar a mesma busca duas vezes
    atualiza ``last_seen_at`` e acrescenta fontes novas, não duplica obra.
    """
    if not works:
        return
    now = int(time.time())
    keys = [w.work_key for w in works]

    async with db.session() as s:
        existing = {
            row.work_key: row
            for row in (await s.execute(select(Work).where(Work.work_key.in_(keys)))).scalars()
        }
        existing_sources = {
            (row.work_key, row.source)
            for row in (
                await s.execute(select(WorkSource).where(WorkSource.work_key.in_(keys)))
            ).scalars()
        }

        for mw in works:
            p = mw.paper
            payload = p.model_dump(mode="json")
            row = existing.get(mw.work_key)
            if row is None:
                row = Work(work_key=mw.work_key, first_seen_at=now)
                s.add(row)
            row.doi = p.doi or None
            row.arxiv_id = p.arxiv_id or None
            row.openalex_id = p.openalex_id or None
            row.s2_id = p.s2_id or None
            row.title = p.title or None
            row.title_norm = (p.title or "").strip().lower()[:512] or None
            row.year = p.year or None
            row.venue_name = p.journal or p.venue or None
            row.issn_l = p.issn or None
            row.cited_by_count = p.cited_by_count or None
            row.is_open_access = p.is_open_access
            row.integrity_status = mw.integrity_status
            row.payload = payload
            row.last_seen_at = now

            for source, seen_at in mw.seen_in.items():
                if (mw.work_key, source) in existing_sources:
                    continue
                s.add(
                    WorkSource(
                        work_key=mw.work_key,
                        source=source,
                        source_id=p.openalex_id or p.doi or None,
                        retrieved_at=seen_at,
                        raw={},
                    )
                )


async def add_work_items(pack_id: str, works: list[MergedWork], selected_keys: set[str]) -> None:
    """Grava as obras no pack.

    Obras retratadas entram com ``selected=False``: continuam no pack, visíveis
    e recuperáveis, mas fora da seleção que sustenta uma afirmação. O bloqueio é
    contra citação não sinalizada, não contra a existência do registro.
    """
    async with db.session() as s:
        for rank, mw in enumerate(works, start=1):
            s.add(
                PackItem(
                    pack_id=pack_id,
                    item_type="work",
                    ref_key=mw.work_key,
                    rank=rank,
                    selected=mw.work_key in selected_keys,
                    payload={
                        "title": mw.paper.title,
                        "year": mw.paper.year,
                        "doi": mw.paper.doi or None,
                        "venue": mw.paper.journal or mw.paper.venue or None,
                        "issn": mw.paper.issn or None,
                        "authors": [a.name for a in (mw.paper.authors or [])][:12],
                        "abstract": mw.paper.abstract or None,
                        "cited_by": mw.paper.cited_by_count,
                        "is_open_access": mw.paper.is_open_access,
                        "integrity_status": mw.integrity_status,
                        "seen_in": sorted(mw.seen_in),
                        "source_count": mw.source_count,
                    },
                )
            )


async def finalize_pack(
    pack_id: str,
    *,
    counts: dict,
    coverage: dict,
    limitations: list[str],
    status: str = "complete",
) -> None:
    async with db.session() as s:
        pack = await s.get(Pack, pack_id)
        if pack is None:
            return
        pack.counts = counts
        pack.coverage = coverage
        pack.limitations = limitations
        pack.status = status
        pack.updated_at = int(time.time())


async def get_pack(pack_id: str) -> Pack | None:
    async with db.session() as s:
        return await s.get(Pack, pack_id)


async def get_items(
    pack_id: str,
    *,
    item_type: str = "work",
    offset: int = 0,
    limit: int = 25,
    selected_only: bool = False,
) -> tuple[list[PackItem], int]:
    async with db.session() as s:
        base = select(PackItem).where(
            PackItem.pack_id == pack_id, PackItem.item_type == item_type
        )
        if selected_only:
            base = base.where(PackItem.selected.is_(True))

        total = (
            await s.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()
        rows = (
            await s.execute(base.order_by(PackItem.rank).offset(offset).limit(limit))
        ).scalars().all()
        return list(rows), int(total)


async def get_item(pack_id: str, ref_key: str) -> PackItem | None:
    async with db.session() as s:
        return (
            await s.execute(
                select(PackItem).where(
                    PackItem.pack_id == pack_id, PackItem.ref_key == ref_key
                )
            )
        ).scalars().first()
