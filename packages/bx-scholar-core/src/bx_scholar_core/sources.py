"""Search sources available to ``search_papers``, and named presets of them.

Adding a source means adding one ``SearchSource`` to ``SEARCH_SOURCES``: an
async function that takes the pool and a ``SearchQuery`` and returns
``(papers, total_hits)``. A source that needs an API key names the Settings
field in ``requires``; without it the source is reported as skipped instead
of failing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bx_scholar_core.models.paper import Paper

if TYPE_CHECKING:
    from bx_scholar_core.clients.pool import ClientPool


@dataclass(frozen=True)
class SearchQuery:
    text: str
    year_from: int | None = None
    year_to: int | None = None
    limit: int = 25
    journal_issn: str | None = None
    sort: str = "cited_by_count:desc"


SearchFn = Callable[["ClientPool", SearchQuery], Awaitable[tuple[list[Paper], int]]]


@dataclass(frozen=True)
class SearchSource:
    name: str
    run: SearchFn
    requires: str | None = None  # Settings field that must be non-empty

    def missing_config(self, pool: ClientPool) -> str | None:
        if self.requires and not getattr(pool.settings, self.requires):
            return f"{self.requires.upper()} not set"
        return None


async def _openalex(pool: ClientPool, q: SearchQuery) -> tuple[list[Paper], int]:
    return await pool.openalex.search(
        q.text, q.year_from, q.year_to, q.journal_issn, sort=q.sort, per_page=q.limit
    )


async def _crossref(pool: ClientPool, q: SearchQuery) -> tuple[list[Paper], int]:
    return await pool.crossref.search(q.text, q.year_from, q.year_to, rows=q.limit)


async def _arxiv(pool: ClientPool, q: SearchQuery) -> tuple[list[Paper], int]:
    papers = await pool.arxiv.search(q.text, max_results=min(q.limit, 20))
    return papers, len(papers)


async def _scielo(pool: ClientPool, q: SearchQuery) -> tuple[list[Paper], int]:
    papers = await pool.scielo.search(q.text, q.year_from, q.year_to, max_results=q.limit)
    return papers, len(papers)


async def _semantic_scholar(pool: ClientPool, q: SearchQuery) -> tuple[list[Paper], int]:
    year = None
    if q.year_from and q.year_to:
        year = f"{q.year_from}-{q.year_to}"
    elif q.year_from:
        year = f"{q.year_from}-"
    elif q.year_to:
        year = f"-{q.year_to}"
    return await pool.semantic_scholar.search(q.text, year=year, limit=q.limit)


async def _tavily(pool: ClientPool, q: SearchQuery) -> tuple[list[Paper], int]:
    results = await pool.tavily.search(q.text, max_results=min(q.limit, 10))
    papers = [
        Paper(
            title=r["title"],
            abstract=r["content"],
            landing_url=r["url"],
            source_type="web",
            source_api="tavily",
        )
        for r in results
    ]
    return papers, len(papers)


SEARCH_SOURCES: dict[str, SearchSource] = {
    s.name: s
    for s in (
        SearchSource("openalex", _openalex),
        SearchSource("crossref", _crossref),
        SearchSource("arxiv", _arxiv),
        SearchSource("scielo", _scielo),
        SearchSource("semantic_scholar", _semantic_scholar),
        SearchSource("tavily", _tavily, requires="tavily_api_key"),
    )
}

# Presets name a set of sources; only entries present in SEARCH_SOURCES are
# used, so a preset can list a source before its client lands.
PRESETS: dict[str, tuple[str, ...]] = {
    "br": ("scielo", "bdtd", "oasisbr"),
    "latam": ("scielo", "lareferencia", "oasisbr"),
    "asia": ("cinii", "jstage", "kci"),
    "bio": ("europepmc", "openalex"),
    "oa": ("core", "openalex"),
}


def expand_sources(spec: str) -> tuple[list[str], list[str]]:
    """Turn "openalex,br" into (known source names in order, unknown names)."""
    names: list[str] = []
    unknown: list[str] = []
    for token in (t.strip().lower() for t in spec.split(",")):
        if not token:
            continue
        for name in PRESETS.get(token, (token,)):
            if name in SEARCH_SOURCES:
                if name not in names:
                    names.append(name)
            elif token not in PRESETS:
                unknown.append(name)
    return names, unknown
