"""Tests for CacheStore — DuckDB-backed cache for API responses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bx_scholar_core.cache.store import CacheStore, make_cache_key


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
    async def test_put_and_get(self, cache_store: CacheStore) -> None:
        await cache_store.put("k1", "paper_metadata", b'{"title":"Test"}', ttl=3600)
        result = await cache_store.get("k1")
        assert result == b'{"title":"Test"}'

    async def test_get_missing_key(self, cache_store: CacheStore) -> None:
        result = await cache_store.get("nonexistent")
        assert result is None

    async def test_expired_entry_returns_none(self, cache_store: CacheStore) -> None:
        expires = datetime.now(UTC) - timedelta(seconds=10)
        cache_store._conn.execute(
            "INSERT INTO cache (key, entity_type, value, expires_at) VALUES (?, ?, ?, ?)",
            ["expired_key", "test", b"old_data", expires],
        )
        result = await cache_store.get("expired_key")
        assert result is None

    async def test_put_replaces_existing(self, cache_store: CacheStore) -> None:
        await cache_store.put("k1", "test", b"v1", ttl=3600)
        await cache_store.put("k1", "test", b"v2", ttl=3600)
        result = await cache_store.get("k1")
        assert result == b"v2"

    async def test_clear(self, cache_store: CacheStore) -> None:
        await cache_store.put("k1", "test", b"v1", ttl=3600)
        await cache_store.put("k2", "test", b"v2", ttl=3600)
        await cache_store.clear()
        assert await cache_store.get("k1") is None
        assert await cache_store.get("k2") is None

    async def test_evict_expired(self, cache_store: CacheStore) -> None:
        await cache_store.put("valid", "test", b"ok", ttl=3600)
        expires = datetime.now(UTC) - timedelta(seconds=10)
        cache_store._conn.execute(
            "INSERT INTO cache (key, entity_type, value, expires_at) VALUES (?, ?, ?, ?)",
            ["expired", "test", b"old", expires],
        )
        await cache_store.evict_expired()
        assert await cache_store.get("valid") == b"ok"
        assert await cache_store.get("expired") is None

    async def test_stats(self, cache_store: CacheStore) -> None:
        await cache_store.put("k1", "paper_metadata", b"v1", ttl=3600)
        await cache_store.put("k2", "paper_metadata", b"v2", ttl=3600)
        await cache_store.put("k3", "search_results", b"v3", ttl=3600)
        s = await cache_store.stats()
        assert s["total"] == 3
        assert s["entries"]["paper_metadata"]["total"] == 2
        assert s["entries"]["search_results"]["total"] == 1

    async def test_stats_empty(self, cache_store: CacheStore) -> None:
        s = await cache_store.stats()
        assert s["total"] == 0
        assert s["entries"] == {}

    async def test_close_idempotent(self, cache_store: CacheStore) -> None:
        await cache_store.close()
        await cache_store.close()  # should not raise

    async def test_file_based_store(self, tmp_path) -> None:
        db_path = tmp_path / "test_cache.duckdb"
        s = CacheStore(db_path=db_path)
        await s.put("k1", "test", b"data", ttl=3600)
        result = await s.get("k1")
        assert result == b"data"
        await s.close()
        assert db_path.exists()
