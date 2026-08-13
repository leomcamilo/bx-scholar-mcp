"""Adaptadores de conector — camada 1, invisível ao agente.

Cada fonte tem sua própria assinatura de busca no ``bx-scholar-core``
(``SemanticScholarClient`` quer o ano como string ``"2020-2024"``, o ArXiv não
aceita recorte de ano, o SciELO tem outro caminho). O agente não deve saber
disso, e o orquestrador também não: aqui todas viram a mesma função.

Esta é a fronteira que o v1 não tinha. Lá, "chame o CrossRef e depois o
OpenAlex" era decisão exposta ao modelo — seis tools de busca, uma por fonte.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from bx_scholar_core.clients.arxiv import ArXivClient
from bx_scholar_core.clients.crossref import CrossRefClient
from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.clients.profile import profile_for
from bx_scholar_core.clients.scielo import SciELOClient
from bx_scholar_core.clients.semantic_scholar import SemanticScholarClient
from bx_scholar_core.models.paper import Paper

from bx_scholar.cache.policy import Entity, policy


@dataclass
class SearchRequest:
    query: str
    year_from: int | None = None
    year_to: int | None = None
    limit: int = 25
    language: str | None = None
    venue_issn: str | None = None


SearchFn = Callable[["SearchRequest"], Awaitable[tuple[list[Paper], int]]]


@dataclass
class Connector:
    """Uma fonte, com seu orçamento e sua função de busca uniformizada."""

    name: str
    search: SearchFn
    close: Callable[[], Awaitable[None]]
    essential: bool = False


def _year_range_string(req: SearchRequest) -> str | None:
    if req.year_from and req.year_to:
        return f"{req.year_from}-{req.year_to}"
    if req.year_from:
        return f"{req.year_from}-"
    if req.year_to:
        return f"-{req.year_to}"
    return None


def build_connectors(settings, cache, names: list[str]) -> list[Connector]:
    """Instancia os conectores pedidos, já com perfil e cache acoplados.

    Os clientes do core aceitam ``profile=`` de forma aditiva: passar o perfil
    liga circuit breaker e teto de concorrência sem mudar nada mais.
    """
    built: list[Connector] = []
    ua = settings.user_agent

    for name in names:
        prof = profile_for(name)

        if name == "openalex":
            client = OpenAlexClient(settings.polite_email, ua, cache=cache, profile=prof)

            async def _search(req: SearchRequest, c=client) -> tuple[list[Paper], int]:
                return await c.search(
                    req.query,
                    year_from=req.year_from,
                    year_to=req.year_to,
                    journal_issn=req.venue_issn,
                    per_page=req.limit,
                )

        elif name == "crossref":
            client = CrossRefClient(settings.polite_email, ua, cache=cache, profile=prof)

            async def _search(req: SearchRequest, c=client) -> tuple[list[Paper], int]:
                return await c.search(
                    req.query,
                    year_from=req.year_from,
                    year_to=req.year_to,
                    rows=req.limit,
                )

        elif name == "arxiv":
            client = ArXivClient(ua, cache=cache, profile=prof)

            async def _search(req: SearchRequest, c=client) -> tuple[list[Paper], int]:
                # O ArXiv não filtra por ano na API; o recorte é aplicado depois,
                # no merge — e isso é registrado como limitação do pack.
                papers, total = await c.search(req.query, max_results=req.limit)
                return papers, total

        elif name == "scielo":
            client = SciELOClient(ua, cache=cache, profile=prof)

            async def _search(req: SearchRequest, c=client) -> tuple[list[Paper], int]:
                return await c.search(
                    req.query, year_from=req.year_from, year_to=req.year_to, per_page=req.limit
                )

        elif name == "semantic_scholar":
            client = SemanticScholarClient(settings.s2_api_key, ua, cache=cache, profile=prof)

            async def _search(req: SearchRequest, c=client) -> tuple[list[Paper], int]:
                return await c.search(req.query, year=_year_range_string(req), limit=req.limit)

        else:
            continue

        built.append(
            Connector(
                name=name,
                search=_search,
                close=client.close,
                essential=prof.essential,
            )
        )

    return built


# Conjuntos por modo. Não é o agente que escolhe fonte — é o modo que ele pede.
MODE_SOURCES: dict[str, list[str]] = {
    # Uma fonte só, cache quente, resposta em segundos.
    "quick": ["openalex"],
    # Cobertura honesta com teto de tempo; SciELO entra sempre porque é o
    # diferencial brasileiro e porque sem ele a cobertura em português mente.
    "balanced": ["openalex", "crossref", "scielo"],
    # Tudo que for pertinente, assíncrono.
    "deep": ["openalex", "crossref", "scielo", "semantic_scholar", "arxiv"],
}

SEARCH_CACHE_POLICY = policy(Entity.SEARCH_RESULTS)
