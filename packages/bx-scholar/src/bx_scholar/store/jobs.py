"""Jobs do modo deep. A execução assíncrona propriamente dita entra na F5;
aqui está o acesso ao estado, que ``read_pack(section='job')`` já consome."""

from __future__ import annotations

import secrets
import time

from sqlalchemy import select

from bx_scholar.store import db
from bx_scholar.store.models import Job


def new_job_id() -> str:
    return f"jb_{secrets.token_hex(8)}"


async def create_job(pack_id: str, *, mode: str = "deep") -> str:
    job_id = new_job_id()
    async with db.session() as s:
        s.add(Job(job_id=job_id, pack_id=pack_id, mode=mode, state="queued",
                  progress={}, created_at=int(time.time())))
    return job_id


async def job_for_pack(pack_id: str) -> Job | None:
    async with db.session() as s:
        return (
            await s.execute(
                select(Job).where(Job.pack_id == pack_id).order_by(Job.created_at.desc())
            )
        ).scalars().first()


async def update_job(job_id: str, **fields) -> None:
    async with db.session() as s:
        job = await s.get(Job, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
