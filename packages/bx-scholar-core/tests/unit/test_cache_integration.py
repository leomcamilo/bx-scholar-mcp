"""Tests for cache integration in AsyncHTTPClient."""

from __future__ import annotations

import httpx
import pytest

from bx_scholar_core.cache.store import CacheStore, make_cache_key
from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError
from bx_scholar_core.logging import setup_logging

setup_logging(level="WARNING")


class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._call_count < len(self._responses):
            resp = self._responses[self._call_count]
        else:
            resp = self._responses[-1]
        self._call_count += 1
        return resp

    @property
    def call_count(self) -> int:
        return self._call_count


class StubClient(AsyncHTTPClient):
    base_url = "https://api.test.com"
    rate_limit = 100.0
    max_rate_period = 1.0
    max_retries = 3
    timeout = 5.0


@pytest.fixture
async def cache():
    store = CacheStore()  # in-memory
    yield store
    await store.close()


class TestCacheIntegration:
    async def test_cache_miss_makes_http_call(self, cache: CacheStore) -> None:
        transport = MockTransport([httpx.Response(200, content=b'{"ok":true}')])
        client = StubClient(cache=cache)
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.get("/test", cache_policy=("test", 3600))
        assert resp.status_code == 200
        assert transport.call_count == 1
        await client.close()

    async def test_cache_miss_populates_cache(self, cache: CacheStore) -> None:
        transport = MockTransport([httpx.Response(200, content=b'{"data":"fresh"}')])
        client = StubClient(cache=cache)
        client._client = httpx.AsyncClient(transport=transport)

        await client.get("/test", params={"q": "hello"}, cache_policy=("search_results", 3600))

        key = make_cache_key("https://api.test.com/test", {"q": "hello"})
        cached = await cache.get(key)
        assert cached == b'{"data":"fresh"}'
        await client.close()

    async def test_cache_hit_skips_http(self, cache: CacheStore) -> None:
        key = make_cache_key("https://api.test.com/cached", None)
        await cache.put(key, "paper_metadata", b'{"cached":true}', ttl=3600)

        transport = MockTransport([httpx.Response(200, content=b'{"fresh":true}')])
        client = StubClient(cache=cache)
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.get("/cached", cache_policy=("paper_metadata", 3600))
        assert resp.content == b'{"cached":true}'
        assert transport.call_count == 0  # no HTTP call made
        await client.close()

    async def test_no_cache_policy_skips_cache(self, cache: CacheStore) -> None:
        transport = MockTransport([httpx.Response(200, content=b'{"ok":true}')])
        client = StubClient(cache=cache)
        client._client = httpx.AsyncClient(transport=transport)

        await client.get("/test")  # no cache_policy
        assert transport.call_count == 1

        # Cache should be empty
        stats = await cache.stats()
        assert stats["total"] == 0
        await client.close()

    async def test_no_cache_store_works(self) -> None:
        transport = MockTransport([httpx.Response(200, content=b'{"ok":true}')])
        client = StubClient()  # no cache
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.get("/test", cache_policy=("test", 3600))
        assert resp.status_code == 200
        assert transport.call_count == 1
        await client.close()

    async def test_error_not_cached(self, cache: CacheStore) -> None:
        transport = MockTransport([httpx.Response(404)])
        client = StubClient(cache=cache)
        client._client = httpx.AsyncClient(transport=transport)

        with pytest.raises(NonRetryableHTTPError):
            await client.get("/missing", cache_policy=("test", 3600))

        stats = await cache.stats()
        assert stats["total"] == 0
        await client.close()

    async def test_post_cache_hit(self, cache: CacheStore) -> None:
        key = make_cache_key("https://api.test.com/search", {"q": "test"})
        await cache.put(key, "web_search", b'{"results":[]}', ttl=3600)

        transport = MockTransport([httpx.Response(200, content=b'{"fresh":true}')])
        client = StubClient(cache=cache)
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.post("/search", json={"q": "test"}, cache_policy=("web_search", 3600))
        assert resp.content == b'{"results":[]}'
        assert transport.call_count == 0
        await client.close()

    async def test_post_cache_miss_populates(self, cache: CacheStore) -> None:
        transport = MockTransport([httpx.Response(200, content=b'{"results":["a"]}')])
        client = StubClient(cache=cache)
        client._client = httpx.AsyncClient(transport=transport)

        await client.post("/search", json={"q": "ai"}, cache_policy=("web_search", 3600))

        key = make_cache_key("https://api.test.com/search", {"q": "ai"})
        cached = await cache.get(key)
        assert cached == b'{"results":["a"]}'
        await client.close()
