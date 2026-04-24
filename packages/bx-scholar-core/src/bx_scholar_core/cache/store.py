"""DuckDB-backed cache store for API responses."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from bx_scholar_core.logging import get_logger

logger = get_logger(__name__)


def make_cache_key(url: str, params: dict[str, Any] | None = None) -> str:
    """Generate a deterministic cache key from URL + sorted params."""
    normalized = json.dumps(params or {}, sort_keys=True, default=str)
    raw = f"{url}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


class CacheStore:
    """Async-safe DuckDB cache for HTTP API responses.

    DuckDB is not async-native, so all operations run in a single-thread
    executor to avoid blocking the event loop and ensure thread safety.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        path_str = str(db_path) if db_path else ":memory:"
        if path_str != ":memory:":
            Path(path_str).parent.mkdir(parents=True, exist_ok=True)

        self._conn = duckdb.connect(path_str)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cache")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key         TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                value       BLOB NOT NULL,
                created_at  TIMESTAMPTZ DEFAULT current_timestamp,
                expires_at  TIMESTAMPTZ NOT NULL
            )
        """)

    async def get(self, key: str) -> bytes | None:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._get_sync, key)

    def _get_sync(self, key: str) -> bytes | None:
        result = self._conn.execute(
            "SELECT value FROM cache WHERE key = ? AND expires_at > current_timestamp",
            [key],
        ).fetchone()
        if result is None:
            return None
        val = result[0]
        return bytes(val) if not isinstance(val, bytes) else val

    async def put(self, key: str, entity_type: str, value: bytes, ttl: int) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._put_sync, key, entity_type, value, ttl)

    def _put_sync(self, key: str, entity_type: str, value: bytes, ttl: int) -> None:
        expires = datetime.now(UTC) + timedelta(seconds=ttl)
        self._conn.execute(
            """INSERT OR REPLACE INTO cache (key, entity_type, value, expires_at)
               VALUES (?, ?, ?, ?)""",
            [key, entity_type, value, expires],
        )

    async def evict_expired(self) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._evict_sync)

    def _evict_sync(self) -> int:
        count = self._conn.execute(
            "SELECT COUNT(*) FROM cache WHERE expires_at <= current_timestamp"
        ).fetchone()[0]
        if count > 0:
            self._conn.execute("DELETE FROM cache WHERE expires_at <= current_timestamp")
        return count

    async def clear(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._clear_sync)

    def _clear_sync(self) -> None:
        self._conn.execute("DELETE FROM cache")

    async def stats(self) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._stats_sync)

    def _stats_sync(self) -> dict[str, Any]:
        rows = self._conn.execute("""
            SELECT entity_type, COUNT(*) as total,
                   SUM(CASE WHEN expires_at > current_timestamp THEN 1 ELSE 0 END) as valid
            FROM cache GROUP BY entity_type
        """).fetchall()
        return {
            "entries": {r[0]: {"total": r[1], "valid": r[2]} for r in rows},
            "total": sum(r[1] for r in rows),
        }

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()
        self._executor.shutdown(wait=False)
