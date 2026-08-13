"""Base async HTTP client with retry, backoff, rate limiting, and optional caching."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from bx_scholar_core.logging import get_logger

if TYPE_CHECKING:
    from bx_scholar_core.cache.store import CacheStore
    from bx_scholar_core.clients.profile import ConnectorProfile

logger = get_logger(__name__)


class RetryableHTTPError(Exception):
    """HTTP error that should be retried."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(message or f"HTTP {status_code}")


class NonRetryableHTTPError(Exception):
    """HTTP error that should NOT be retried."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(message or f"HTTP {status_code}")


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RetryableHTTPError, httpx.ConnectTimeout, httpx.ReadTimeout))


class AsyncHTTPClient:
    """Base HTTP client with retry, backoff, per-host rate limiting, and optional caching.

    Subclasses set `base_url`, `rate_limit`, and `max_rate_period` to configure
    behavior for specific APIs.
    """

    base_url: str = ""
    rate_limit: float = 10.0  # requests per period
    max_rate_period: float = 1.0  # period in seconds
    max_retries: int = 3
    timeout: float = 30.0

    #: Nome do perfil em ``clients.profile.PROFILES``. Subclasses podem definir;
    #: quando ausente, o cliente segue com os atributos de classe acima.
    profile_name: str = ""

    def __init__(
        self,
        user_agent: str = "BX-Scholar/0.1.0",
        cache: CacheStore | None = None,
        profile: ConnectorProfile | None = None,
    ) -> None:
        """``profile`` é opcional e aditivo.

        Sem perfil, o comportamento é idêntico ao v1 (atributos de classe,
        sem breaker e sem teto de concorrência) — é o que mantém o servidor
        legado funcionando sem alteração. Com perfil, o cliente ganha limite de
        concorrência e circuit breaker.
        """
        if profile is None and self.profile_name:
            from bx_scholar_core.clients.profile import profile_for

            profile = profile_for(self.profile_name)

        self._profile = profile
        self._user_agent = user_agent
        rate = profile.rate_limit if profile else self.rate_limit
        period = profile.rate_period if profile else self.max_rate_period
        self._limiter = AsyncLimiter(rate, period)
        self._semaphore = asyncio.Semaphore(profile.max_concurrency) if profile else None
        self._breaker = profile.breaker() if profile else None
        self._client: httpx.AsyncClient | None = None
        self._cache = cache

    @property
    def effective_timeout(self) -> float:
        return self._profile.timeout if self._profile else self.timeout

    @property
    def effective_retries(self) -> int:
        return self._profile.max_retries if self._profile else self.max_retries

    @property
    def circuit(self) -> dict | None:
        """Estado do breaker, para o bloco ``coverage`` da resposta."""
        return self._breaker.snapshot() if self._breaker else None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.effective_timeout,
                follow_redirects=True,
                headers={"User-Agent": self._user_agent},
            )
        return self._client

    @asynccontextmanager
    async def _guarded(self) -> AsyncIterator[None]:
        """Breaker + teto de concorrência em volta de uma chamada completa.

        O breaker envolve a chamada INTEIRA, não cada tentativa: três retries de
        uma mesma requisição contam como uma falha, senão um único pedido a uma
        fonte fora do ar já abriria o circuito com ``failure_threshold=3``.
        """
        if self._breaker is None:
            yield
            return

        self._breaker.allow()  # levanta CircuitOpenError se recusado
        try:
            if self._semaphore is not None:
                async with self._semaphore:
                    yield
            else:
                yield
        except Exception:
            self._breaker.record_failure()
            raise
        else:
            self._breaker.record_success()

    def _extra_headers(self) -> dict[str, str]:
        """Override in subclasses to add API-key headers, etc."""
        return {}

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        *,
        cache_policy: tuple[str, int] | None = None,
    ) -> httpx.Response:
        """Make a GET request with rate limiting, retry, and optional caching.

        cache_policy: optional (entity_type, ttl_seconds) tuple.
        If set and a CacheStore is attached, responses are cached and reused.
        """
        full_url = f"{self.base_url}{url}" if self.base_url and not url.startswith("http") else url

        # Check cache before making HTTP request
        if self._cache and cache_policy:
            from bx_scholar_core.cache.store import make_cache_key

            ck = make_cache_key(full_url, params)
            cached = await self._cache.get(ck)
            if cached is not None:
                logger.debug("cache_hit", url=full_url, entity_type=cache_policy[0])
                return httpx.Response(200, content=cached)

        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self.effective_retries),
            wait=wait_exponential_jitter(initial=1, max=30, jitter=2),
            reraise=True,
        )
        async def _do_request() -> httpx.Response:
            async with self._limiter:
                client = await self._get_client()
                merged_headers = {**self._extra_headers(), **(headers or {})}
                t0 = time.monotonic()
                resp = await client.get(full_url, params=params, headers=merged_headers)
                elapsed_ms = (time.monotonic() - t0) * 1000

                logger.debug(
                    "http_request",
                    method="GET",
                    url=full_url,
                    status=resp.status_code,
                    elapsed_ms=round(elapsed_ms, 1),
                )

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    msg = f"Rate limited (429). Retry-After: {retry_after}"
                    logger.warning("rate_limited", url=full_url, retry_after=retry_after)
                    if retry_after:
                        try:  # noqa: SIM105
                            await asyncio.sleep(min(float(retry_after), 60))
                        except ValueError:
                            pass
                    raise RetryableHTTPError(429, msg)

                if resp.status_code >= 500:
                    msg = f"Server error ({resp.status_code})"
                    logger.warning("server_error", url=full_url, status=resp.status_code)
                    raise RetryableHTTPError(resp.status_code, msg)

                if resp.status_code >= 400:
                    raise NonRetryableHTTPError(resp.status_code)

                return resp

        async with self._guarded():
            resp = await _do_request()

        # Store successful response in cache
        if self._cache and cache_policy:
            from bx_scholar_core.cache.store import make_cache_key

            ck = make_cache_key(full_url, params)
            entity_type, ttl = cache_policy
            await self._cache.put(ck, entity_type, resp.content, ttl)

        return resp

    async def post(
        self,
        url: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        *,
        cache_policy: tuple[str, int] | None = None,
    ) -> httpx.Response:
        """Make a POST request with rate limiting, retry, and optional caching."""
        full_url = f"{self.base_url}{url}" if self.base_url and not url.startswith("http") else url

        # Check cache before making HTTP request
        if self._cache and cache_policy:
            from bx_scholar_core.cache.store import make_cache_key

            ck = make_cache_key(full_url, json)
            cached = await self._cache.get(ck)
            if cached is not None:
                logger.debug("cache_hit", url=full_url, entity_type=cache_policy[0])
                return httpx.Response(200, content=cached)

        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self.effective_retries),
            wait=wait_exponential_jitter(initial=1, max=30, jitter=2),
            reraise=True,
        )
        async def _do_request() -> httpx.Response:
            async with self._limiter:
                client = await self._get_client()
                merged_headers = {**self._extra_headers(), **(headers or {})}
                resp = await client.post(full_url, json=json, headers=merged_headers)

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:  # noqa: SIM105
                            await asyncio.sleep(min(float(retry_after), 60))
                        except ValueError:
                            pass
                    raise RetryableHTTPError(429)
                if resp.status_code >= 500:
                    raise RetryableHTTPError(resp.status_code)
                if resp.status_code >= 400:
                    raise NonRetryableHTTPError(resp.status_code)

                return resp

        async with self._guarded():
            resp = await _do_request()

        # Store successful response in cache
        if self._cache and cache_policy:
            from bx_scholar_core.cache.store import make_cache_key

            ck = make_cache_key(full_url, json)
            entity_type, ttl = cache_policy
            await self._cache.put(ck, entity_type, resp.content, ttl)

        return resp
