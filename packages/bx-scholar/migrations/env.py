"""Ambiente Alembic — assíncrono e dialeto-agnóstico.

Uma só configuração serve SQLite e Postgres: a URL vem de
``BX_SCHOLAR_DATABASE_URL`` e o engine é async nos dois casos (``aiosqlite`` ou
``asyncpg``). ``render_as_batch`` fica ligado porque o SQLite não faz
``ALTER COLUMN`` — sem isso, qualquer migração futura que altere coluna
quebraria só no dialeto intermediário, que é justamente onde o v2 começa.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from bx_scholar.store.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "bx-scholar"


def _database_url() -> str:
    url = os.environ.get("BX_SCHOLAR_DATABASE_URL", "").strip()
    if url:
        return url
    _DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{_DEFAULT_DB_DIR / 'bx_scholar.db'}"


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()

    connectable = async_engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
