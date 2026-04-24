"""Tests for CacheStore — DuckDB-backed cache for API responses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bx_scholar_core.cache.store import CacheStore, make_cache_key
from bx_scholar_core.logging import setup_logging

setup_logging(level="WARNING")


@pytest.fixture
async def store():
    s = CacheStore()  # in-memory
    yield s
    await s.close()


class TestMakeCacheKey:
    def test_deterministic(self) -> None:
        k1 = make_cache_key("https://api.example.com/v1", {"q": "test", "page": 1})
        k2 = make_cache_key("https://api.example.com/v1", {"q": "test", "page": 1})
        assert k1 == k2

    def test_order_independent(self) -> None:
        k1 = make_cache_key("https://x.com", {"b": 2, "a": 1})
        k2 = make_cache_key("https://x.com", {"a": 1, "b": 2})
        assert k1 == k2

    def test_different_params(self) -> None:
        k1 = make_cache_key("https://x.com", {"q": "alpha"})
        k2 = make_cache_key("https://x.com", {"q": "beta"})
        assert k1 != k2

    def test_different_urls(self) -> None:
        k1 = make_cache_key("https://a.com/v1", {"q": "test"})
        k2 = make_cache_key("https://b.com/v1", {"q": "test"})
        assert k1 != k2

    def test_none_params(self) -> None:
        k1 = make_cache_key("https://x.com", None)
        k2 = make_cache_key("https://x.com", {})
        assert k1 == k2

    def test_returns_hex_string(self) -> None:
        k = make_cache_key("https://x.com")
        assert len(k) == 64  # SHA-256 hex


class TestCacheStore:
    async def test_put_and_get(self, store: CacheStore) -> None:
        await store.put("k1", "paper_metadata", b'{"title":"Test"}', ttl=3600)
        result = await store.get("k1")
        assert result == b'{"title":"Test"}'

    async def test_get_missing_key(self, store: CacheStore) -> None:
        result = await store.get("nonexistent")
        assert result is None

    async def test_expired_entry_returns_none(self, store: CacheStore) -> None:
        # Insert with already-expired timestamp via sync method
        expires = datetime.now(UTC) - timedelta(seconds=10)
        store._conn.execute(
            "INSERT INTO cache (key, entity_type, value, expires_at) VALUES (?, ?, ?, ?)",
            ["expired_key", "test", b"old_data", expires],
        )
        result = await store.get("expired_key")
        assert result is None

    async def test_put_replaces_existing(self, store: CacheStore) -> None:
        await store.put("k1", "test", b"v1", ttl=3600)
        await store.put("k1", "test", b"v2", ttl=3600)
        result = await store.get("k1")
        assert result == b"v2"

    async def test_clear(self, store: CacheStore) -> None:
        await store.put("k1", "test", b"v1", ttl=3600)
        await store.put("k2", "test", b"v2", ttl=3600)
        await store.clear()
        assert await store.get("k1") is None
        assert await store.get("k2") is None

    async def test_evict_expired(self, store: CacheStore) -> None:
        # Insert one valid and one expired entry
        await store.put("valid", "test", b"ok", ttl=3600)
        expires = datetime.now(UTC) - timedelta(seconds=10)
        store._conn.execute(
            "INSERT INTO cache (key, entity_type, value, expires_at) VALUES (?, ?, ?, ?)",
            ["expired", "test", b"old", expires],
        )
        await store.evict_expired()
        assert await store.get("valid") == b"ok"
        assert await store.get("expired") is None

    async def test_stats(self, store: CacheStore) -> None:
        await store.put("k1", "paper_metadata", b"v1", ttl=3600)
        await store.put("k2", "paper_metadata", b"v2", ttl=3600)
        await store.put("k3", "search_results", b"v3", ttl=3600)
        s = await store.stats()
        assert s["total"] == 3
        assert s["entries"]["paper_metadata"]["total"] == 2
        assert s["entries"]["search_results"]["total"] == 1

    async def test_stats_empty(self, store: CacheStore) -> None:
        s = await store.stats()
        assert s["total"] == 0
        assert s["entries"] == {}

    async def test_close_idempotent(self, store: CacheStore) -> None:
        await store.close()
        await store.close()  # should not raise

    async def test_file_based_store(self, tmp_path) -> None:
        db_path = tmp_path / "test_cache.duckdb"
        s = CacheStore(db_path=db_path)
        await s.put("k1", "test", b"data", ttl=3600)
        result = await s.get("k1")
        assert result == b"data"
        await s.close()
        assert db_path.exists()
