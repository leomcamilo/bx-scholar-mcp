"""Tests for bx_scholar_core.clients.base."""

from __future__ import annotations

import httpx
import pytest

from bx_scholar_core.clients.base import (
    NonRetryableHTTPError,
    RetryableHTTPError,
)
from tests.conftest import MockTransport, StubClient


class TestAsyncHTTPClient:
    async def test_successful_get(self) -> None:
        transport = MockTransport(
            [
                httpx.Response(200, json={"ok": True}),
            ]
        )
        client = StubClient()
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.get("/test")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert transport.call_count == 1
        await client.close()

    async def test_404_no_retry(self) -> None:
        transport = MockTransport(
            [
                httpx.Response(404),
            ]
        )
        client = StubClient()
        client._client = httpx.AsyncClient(transport=transport)

        with pytest.raises(NonRetryableHTTPError) as exc_info:
            await client.get("/missing")
        assert exc_info.value.status_code == 404
        assert transport.call_count == 1  # no retry for 4xx
        await client.close()

    async def test_500_retries(self) -> None:
        transport = MockTransport(
            [
                httpx.Response(500),
                httpx.Response(500),
                httpx.Response(200, json={"recovered": True}),
            ]
        )
        client = StubClient()
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.get("/flaky")
        assert resp.status_code == 200
        assert transport.call_count == 3  # 2 failures + 1 success
        await client.close()

    async def test_500_exhausts_retries(self) -> None:
        transport = MockTransport(
            [
                httpx.Response(500),
                httpx.Response(502),
                httpx.Response(503),
            ]
        )
        client = StubClient()
        client._client = httpx.AsyncClient(transport=transport)

        with pytest.raises(RetryableHTTPError):
            await client.get("/always-fails")
        assert transport.call_count == 3  # exhausted all retries
        await client.close()

    async def test_429_retries(self) -> None:
        transport = MockTransport(
            [
                httpx.Response(429, headers={"Retry-After": "1"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        client = StubClient()
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.get("/rate-limited")
        assert resp.status_code == 200
        assert transport.call_count == 2
        await client.close()

    async def test_full_url_passthrough(self) -> None:
        transport = MockTransport(
            [
                httpx.Response(200, json={"ok": True}),
            ]
        )
        client = StubClient()
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.get("https://other.api.com/endpoint")
        assert resp.status_code == 200
        await client.close()

    async def test_post_success(self) -> None:
        transport = MockTransport(
            [
                httpx.Response(200, json={"created": True}),
            ]
        )
        client = StubClient()
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.post("/create", json={"name": "test"})
        assert resp.status_code == 200
        await client.close()

    async def test_post_429_retries(self) -> None:
        transport = MockTransport(
            [
                httpx.Response(429),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        client = StubClient()
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.post("/submit", json={"data": 1})
        assert resp.status_code == 200
        assert transport.call_count == 2
        await client.close()

    async def test_extra_headers(self) -> None:
        """Subclass can inject extra headers."""

        class AuthClient(StubClient):
            def _extra_headers(self) -> dict[str, str]:
                return {"x-api-key": "secret123"}

        transport = MockTransport(
            [
                httpx.Response(200, json={"ok": True}),
            ]
        )
        client = AuthClient()
        client._client = httpx.AsyncClient(transport=transport)

        resp = await client.get("/authed")
        assert resp.status_code == 200
        await client.close()

    async def test_close_idempotent(self) -> None:
        client = StubClient()
        await client.close()  # no-op, no client created
        await client.close()  # still no-op


class TestQuotaExhausted:
    async def test_long_retry_after_fails_at_once(self) -> None:
        """OpenAlex answers an exhausted daily budget with 429 and Retry-After of
        hours; the client must not sleep and retry for minutes inside a tool call."""
        import time

        from bx_scholar_core.clients.base import QuotaExhaustedError

        calls = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(429, headers={"Retry-After": "20364"}, json={"error": "budget"})

        client = StubClient()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        t0 = time.monotonic()
        with pytest.raises(QuotaExhaustedError, match=r"resets in 5\.7 h"):
            await client.get("/x")
        assert time.monotonic() - t0 < 1
        assert calls == 1
        await client.close()


class TestOpenAlexApiKey:
    async def test_key_sent_only_when_configured(self) -> None:
        from bx_scholar_core.clients.openalex import OpenAlexClient

        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(200, json={"results": [], "meta": {"count": 0}})

        for key in ("", "k123"):
            client = OpenAlexClient("ci@bxscholar.dev", api_key=key)
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            await client.search("x")
            await client.close()
        assert "api_key" not in seen[0].url.params
        assert seen[1].url.params["api_key"] == "k123"
