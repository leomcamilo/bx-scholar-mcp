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
from bx_scholar_core.clients.brasil import BrasilClient
from bx_scholar_core.clients.crossref import CrossRefClient
from bx_scholar_core.clients.europepmc import EuropePMCClient
from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.clients.profile import profile_for
from bx_scholar_core.clients.semantic_scholar import SemanticScholarClient
from bx_scholar_core.models.paper import Paper


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


def _within_year_range(
    papers: list[Paper], year_from: int | None, year_to: int | None
) -> list[Paper]:
    """Aplica o recorte de ano para fontes que não filtram na API.

    Obra sem ano declarado é MANTIDA: descartá-la trocaria um erro visível
    (resultado fora da faixa) por um invisível (obra pertinente sumindo porque
    a base não informou o ano).
    """
    if not year_from and not year_to:
        return papers
    out = []
    for p in papers:
        if p.year is None:
            out.append(p)
            continue
        if year_from and p.year < year_from:
            continue
        if year_to and p.year > year_to:
            continue
        out.append(p)
    return out


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
                    # O core tem `sort="cited_by_count:desc"` como PADRÃO
                    # (clients/openalex.py:101) — a busca acadêmica principal
                    # ordenando por citação em vez de relevância. Na prática
                    # isso devolve o artigo mais citado que casa vagamente com
                    # os termos, não o artigo sobre o tema perguntado: uma busca
                    # por "mobilidade urbana preditiva" traz indústria 4.0 de
                    # 2018. Aqui pedimos relevância explicitamente; o padrão do
                    # core não é tocado para não alterar o v1 em produção.
                    sort="relevance_score:desc",
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
                # A API do arXiv não filtra por ano. Antes, nada filtrava depois
                # tampouco: um pedido de 2020-2024 devolvia qualquer ano, sem
                # aviso. O recorte é aplicado aqui, e o descarte fica visível no
                # total reportado — omitir seria o mesmo silêncio que o bloco
                # `coverage` existe para evitar.
                papers, total = await c.search(req.query, max_results=req.limit)
                kept = _within_year_range(papers, req.year_from, req.year_to)
                return kept, total

        elif name == "brasil":
            client = BrasilClient(settings.polite_email, ua, cache=cache, profile=prof)

            async def _search(req: SearchRequest, c=client) -> tuple[list[Paper], int]:
                return await c.search(
                    req.query,
                    year_from=req.year_from,
                    year_to=req.year_to,
                    limit=req.limit,
                    language=req.language or "pt",
                )

        elif name == "europepmc":
            client = EuropePMCClient(settings.polite_email, ua, cache=cache, profile=prof)

            async def _search(req: SearchRequest, c=client) -> tuple[list[Paper], int]:
                return await c.search(
                    req.query,
                    year_from=req.year_from,
                    year_to=req.year_to,
                    limit=req.limit,
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
    # Cobertura honesta com teto de tempo. `brasil` entra sempre: sem o eixo de
    # idioma, a cobertura em português MENTE — o filtro de publisher SciELO do
    # OpenAlex devolve 63 resultados onde `language:pt` devolve 62.176.
    # `europepmc` entra aqui e não só no deep: é a ÚNICA fonte biomédica (o v1
    # não tinha nenhuma) e a única que entrega texto integral com seções
    # nomeadas, que é o que sustenta verification_basis="fulltext". As quatro
    # rodam em paralelo com teto próprio, então o custo é de uma requisição, não
    # de mais tempo de parede.
    "balanced": ["openalex", "crossref", "brasil", "europepmc"],
    "deep": ["openalex", "crossref", "brasil", "europepmc", "semantic_scholar", "arxiv"],
}

