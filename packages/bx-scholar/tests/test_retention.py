"""Expurgo do store — apaga o artefato, preserva o ativo."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import func, select

from bx_scholar.store import db, retention
from bx_scholar.store.models import (
    FulltextDoc,
    FulltextSpan,
    Integrity,
    Pack,
    PackItem,
    Venue,
    Work,
    WorkSource,
)

DAY = 86_400


async def seed(*, pack_age_days: int, pack_id: str, work_key: str, doc_id: str) -> None:
    now = int(time.time())
    async with db.session() as s:
        s.add(Pack(pack_id=pack_id, kind="search", status="complete", query={},
                   counts={}, coverage={}, limitations=[], provenance={},
                   workflow_version="v", created_at=now - pack_age_days * DAY,
                   updated_at=now))
        s.add(PackItem(pack_id=pack_id, item_type="work", ref_key=work_key,
                       rank=1, payload={"title": "t"}))
        s.add(Work(work_key=work_key, title="t", payload={}))
        s.add(WorkSource(work_key=work_key, source="openalex", retrieved_at=now, raw={}))
        s.add(FulltextDoc(doc_id=doc_id, work_key=work_key, access_status="open_fulltext",
                          sections=[], retrieved_at=now))
        s.add(FulltextSpan(span_id=f"sp_{doc_id}", doc_id=doc_id, ordinal=0,
                           text="x" * 5000))


async def counts() -> dict[str, int]:
    async with db.session() as s:
        out = {}
        for name, model in (("pack", Pack), ("pack_item", PackItem), ("work", Work),
                            ("work_source", WorkSource), ("doc", FulltextDoc),
                            ("span", FulltextSpan), ("venue", Venue),
                            ("integrity", Integrity)):
            out[name] = int(
                (await s.execute(select(func.count()).select_from(model))).scalar_one()
            )
        return out


class TestPrune:
    async def test_old_pack_goes_recent_stays(self, store) -> None:
        await seed(pack_age_days=120, pack_id="pk_velho", work_key="doi:10.1/a", doc_id="doc_a")
        await seed(pack_age_days=1, pack_id="pk_novo", work_key="doi:10.1/b", doc_id="doc_b")

        report = await retention.prune(days=90)

        assert report.packs == 1
        async with db.session() as s:
            remaining = (await s.execute(select(Pack.pack_id))).scalars().all()
        assert remaining == ["pk_novo"]

    async def test_entity_store_is_preserved(self, store) -> None:
        """O ativo fica: apagar work/work_source obrigaria a rebuscar na rede,
        e a proveniência com timestamp não é reconstruível depois."""
        await seed(pack_age_days=120, pack_id="pk_velho", work_key="doi:10.1/a", doc_id="doc_a")
        async with db.session() as s:
            s.add(Venue(name="Rev", name_norm="rev"))
            s.add(Integrity(doi="10.1/x", status="retracted"))

        await retention.prune(days=90)

        after = await counts()
        assert after["work"] == 1
        assert after["work_source"] == 1
        assert after["venue"] == 1
        assert after["integrity"] == 1

    async def test_orphan_fulltext_is_removed(self, store) -> None:
        await seed(pack_age_days=120, pack_id="pk_velho", work_key="doi:10.1/a", doc_id="doc_a")
        await retention.prune(days=90)
        after = await counts()
        assert after["doc"] == 0
        assert after["span"] == 0

    async def test_fulltext_still_referenced_by_a_recent_pack_survives(self, store) -> None:
        """A obra aparece num pack velho E num recente — o texto tem de ficar."""
        await seed(pack_age_days=120, pack_id="pk_velho", work_key="doi:10.1/a", doc_id="doc_a")
        now = int(time.time())
        async with db.session() as s:
            s.add(Pack(pack_id="pk_novo", kind="search", status="complete", query={},
                       counts={}, coverage={}, limitations=[], provenance={},
                       workflow_version="v", created_at=now, updated_at=now))
            s.add(PackItem(pack_id="pk_novo", item_type="work", ref_key="doi:10.1/a",
                           rank=1, payload={}))

        await retention.prune(days=90)

        after = await counts()
        assert after["doc"] == 1, "documento citado por pack recente não pode sair"
        assert after["span"] == 1

    async def test_dry_run_changes_nothing(self, store) -> None:
        await seed(pack_age_days=120, pack_id="pk_velho", work_key="doi:10.1/a", doc_id="doc_a")
        before = await counts()
        report = await retention.prune(days=90, dry_run=True)
        assert report.packs == 1  # relata
        assert await counts() == before  # mas não apaga

    async def test_zero_days_is_refused(self, store) -> None:
        # Janela zero apagaria tudo, inclusive o pack que acabou de ser criado.
        with pytest.raises(ValueError):
            await retention.prune(days=0)

    async def test_usage_reports_text_volume(self, store) -> None:
        await seed(pack_age_days=1, pack_id="pk", work_key="doi:10.1/a", doc_id="doc_a")
        u = await retention.usage()
        assert u["rows"]["fulltext_span"] == 1
        assert u["fulltext_chars"] == 5000
