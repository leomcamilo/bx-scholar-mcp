"""Engine e sessão do store durável.

Um único ponto onde o dialeto é conhecido. Todo o resto do código fala
SQLAlchemy e não sabe se está num SQLite ou num Postgres — é isso que faz a
migração futura ser uma env var em vez de uma reescrita.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bx_scholar.store.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _tune_sqlite(engine: AsyncEngine) -> None:
    """WAL + foreign keys no SQLite.

    Sem WAL, um leitor bloqueia o escritor e o serviço serializa sob carga. É a
    mesma classe de problema que fez o v1 acabar com DOIS DuckDB de ~90 MB em
    disco: DuckDB toma lock exclusivo do arquivo, então dois processos não
    compartilham e cada um mantém a sua cópia do mesmo dado upstream.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def init_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Cria (uma vez) o engine global."""
    global _engine, _sessionmaker

    if _engine is not None:
        return _engine

    is_sqlite = database_url.startswith("sqlite")
    kwargs: dict = {"echo": echo, "future": True}
    if not is_sqlite:
        # asyncpg gerencia o próprio pool; pre_ping evita servir conexão morta
        # depois de um restart do Postgres.
        kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=5)

    _engine = create_async_engine(database_url, **kwargs)
    if is_sqlite:
        _tune_sqlite(_engine)

    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("store não inicializado — chame init_engine() no startup")
    return _engine


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """Sessão transacional. Commit no sucesso, rollback na exceção."""
    if _sessionmaker is None:
        raise RuntimeError("store não inicializado — chame init_engine() no startup")
    async with _sessionmaker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def create_all() -> None:
    """Cria o schema direto do metadata.

    Atalho para testes e primeiro boot. O caminho oficial de produção é
    ``alembic upgrade head`` — é ele que garante que a migração SQLite→Postgres
    aplique o mesmo schema nos dois dialetos.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    """Derruba o schema. Só para testes — dá isolamento por teste no Postgres,
    onde não há o truque do arquivo temporário do SQLite."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def healthcheck() -> dict:
    """Ping do store, para o log de startup e para o smoke de deploy."""
    engine = get_engine()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"dialect": engine.dialect.name, "url": str(engine.url.render_as_string(hide_password=True))}


async def dispose_pool() -> None:
    """Fecha as conexões do pool SEM descartar o engine.

    Necessário porque o healthcheck de startup roda num ``asyncio.run()``
    próprio, que morre antes de o uvicorn criar o loop definitivo. O asyncpg
    prende cada conexão ao loop em que ela nasceu; sem este descarte, a primeira
    coisa no journal depois de subir é ``RuntimeError: Event loop is closed``,
    vindo do coletor tentando fechar conexões de um loop que não existe mais.

    Com aiosqlite o problema não aparecia — foi o Postgres que o expôs.

    O engine continua utilizável: o SQLAlchemy recria o pool sob demanda, já no
    loop certo.
    """
    if _engine is not None:
        await _engine.dispose()


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
