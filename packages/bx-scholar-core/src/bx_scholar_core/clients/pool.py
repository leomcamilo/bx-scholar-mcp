"""One shared instance of each API client per server.

Rate limits live on the client instance (``AsyncHTTPClient._limiter``), so a
client created per tool call only throttles that call: two concurrent calls
each got a fresh budget. Tools take their clients from this pool instead.

The pool is not closed on shutdown on purpose. Over streamable-http, FastMCP
runs its lifespan once per session, so closing there would drop the clients
other sessions still use; the process exit releases the sockets. ``aclose``
exists for tests and library users.
"""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING, TypeVar

from bx_scholar_core.clients.arxiv import ArXivClient
from bx_scholar_core.clients.core import CoreClient
from bx_scholar_core.clients.crossref import CrossRefClient
from bx_scholar_core.clients.europepmc import EuropePMCClient
from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.clients.opencitations import OpenCitationsClient
from bx_scholar_core.clients.scielo import SciELOClient
from bx_scholar_core.clients.semantic_scholar import SemanticScholarClient
from bx_scholar_core.clients.tavily import TavilyClient
from bx_scholar_core.clients.unpaywall import UnpaywallClient
from bx_scholar_core.clients.vufind import BDTDClient, LAReferenciaClient, OasisBRClient

if TYPE_CHECKING:
    from bx_scholar_core.cache import CacheStore
    from bx_scholar_core.clients.base import AsyncHTTPClient
    from bx_scholar_core.config import Settings

C = TypeVar("C", bound="AsyncHTTPClient")


class ClientPool:
    def __init__(self, settings: Settings, cache: CacheStore | None = None) -> None:
        self.settings = settings
        self.cache = cache
        self._created: list[AsyncHTTPClient] = []

    def _track(self, client: C) -> C:
        self._created.append(client)
        return client

    @cached_property
    def openalex(self) -> OpenAlexClient:
        s = self.settings
        return self._track(
            OpenAlexClient(s.polite_email, s.user_agent, s.openalex_api_key, cache=self.cache)
        )

    @cached_property
    def crossref(self) -> CrossRefClient:
        s = self.settings
        return self._track(CrossRefClient(s.polite_email, s.user_agent, cache=self.cache))

    @cached_property
    def arxiv(self) -> ArXivClient:
        return self._track(ArXivClient(user_agent=self.settings.user_agent, cache=self.cache))

    @cached_property
    def scielo(self) -> SciELOClient:
        s = self.settings
        return self._track(
            SciELOClient(s.polite_email, s.user_agent, s.openalex_api_key, cache=self.cache)
        )

    @cached_property
    def semantic_scholar(self) -> SemanticScholarClient:
        s = self.settings
        return self._track(SemanticScholarClient(s.s2_api_key, s.user_agent, cache=self.cache))

    @cached_property
    def tavily(self) -> TavilyClient:
        s = self.settings
        return self._track(TavilyClient(s.tavily_api_key, s.user_agent, cache=self.cache))

    @cached_property
    def unpaywall(self) -> UnpaywallClient:
        s = self.settings
        return self._track(UnpaywallClient(s.polite_email, s.user_agent, cache=self.cache))

    @cached_property
    def europepmc(self) -> EuropePMCClient:
        return self._track(EuropePMCClient(user_agent=self.settings.user_agent, cache=self.cache))

    @cached_property
    def bdtd(self) -> BDTDClient:
        return self._track(BDTDClient(user_agent=self.settings.user_agent, cache=self.cache))

    @cached_property
    def oasisbr(self) -> OasisBRClient:
        return self._track(OasisBRClient(user_agent=self.settings.user_agent, cache=self.cache))

    @cached_property
    def lareferencia(self) -> LAReferenciaClient:
        ua = self.settings.user_agent
        return self._track(LAReferenciaClient(user_agent=ua, cache=self.cache))

    @cached_property
    def opencitations(self) -> OpenCitationsClient:
        s = self.settings
        return self._track(
            OpenCitationsClient(s.opencitations_token, s.user_agent, cache=self.cache)
        )

    @cached_property
    def core(self) -> CoreClient:
        s = self.settings
        return self._track(CoreClient(s.core_api_key, s.user_agent, cache=self.cache))

    async def aclose(self) -> None:
        for client in self._created:
            await client.close()
