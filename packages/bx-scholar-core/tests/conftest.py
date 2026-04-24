"""Shared test fixtures for bx-scholar-core."""

from __future__ import annotations

import httpx
import pytest

from bx_scholar_core.cache.store import CacheStore
from bx_scholar_core.clients.base import AsyncHTTPClient
from bx_scholar_core.logging import setup_logging

# Suppress noisy logs in tests — session-scoped so it runs once
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


@pytest.fixture
async def cache_store():
    """In-memory CacheStore for testing."""
    store = CacheStore()
    yield store
    await store.close()
