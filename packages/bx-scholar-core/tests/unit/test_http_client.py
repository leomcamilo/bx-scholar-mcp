"""Tests for bx_scholar_core.clients.base."""

from __future__ import annotations

import httpx
import pytest

from bx_scholar_core.clients.base import (
    AsyncHTTPClient,
    NonRetryableHTTPError,
    RetryableHTTPError,
)
from bx_scholar_core.logging import setup_logging

# Ensure logging is set up for tests
setup_logging(level="WARNING")


class MockTransport(httpx.AsyncBaseTransport):
    """Mock transport that returns predefined responses."""

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
    """Test client with fast rate limit for testing."""

    base_url = "https://api.test.com"
    rate_limit = 100.0
    max_rate_period = 1.0
    max_retries = 3
    timeout = 5.0


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
