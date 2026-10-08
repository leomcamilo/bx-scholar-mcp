"""Citation lookups merged across OpenAlex and OpenCitations.

OpenAlex ranks citing works by citation count; OpenCitations holds open
Crossref links, with no ranking and no metadata. OpenAlex supplies the top
``limit`` works, and OpenCitations adds up to ``limit`` works that are not in
that OpenAlex page (most recent first), resolved through its Meta API.

"Not in the OpenAlex page" is not "unknown to OpenAlex": OpenAlex may hold the
work further down its ranking. Coverage is compared through ``total_links``,
the number of links each source reports for the DOI. ``citation_sources`` on a
paper names the sources that returned it in this response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from bx_scholar_core.clients.openalex import _parse_work
from bx_scholar_core.dedup import deduplicate
from bx_scholar_core.id_resolver import is_valid_doi, resolve_id
from bx_scholar_core.logging import get_logger

if TYPE_CHECKING:
    from bx_scholar_core.clients.opencitations import CitationLink
    from bx_scholar_core.clients.pool import ClientPool
    from bx_scholar_core.models.paper import Paper

logger = get_logger(__name__)

CITATION_SOURCES = ("openalex", "opencitations")
Direction = Literal["citing", "references"]


def parse_sources(spec: str) -> tuple[list[str], list[str]]:
    """'openalex,opencitations' -> (known, unknown)."""
    names = [s.strip().lower() for s in spec.split(",") if s.strip()]
    return [n for n in names if n in CITATION_SOURCES], [
        n for n in names if n not in CITATION_SOURCES
    ]


async def resolve_to_doi(pool: ClientPool, identifier: str) -> str | None:
    """DOI for a DOI, OpenAlex ID, PMID or arXiv ID; None if the work has no DOI.

    Citation APIs are keyed by DOI. OpenAlex resolves the other identifiers
    (arXiv preprints carry DataCite DOIs 10.48550/arXiv.<id>).
    """
    resolved = resolve_id(identifier)
    if resolved.id_type == "doi":
        return resolved.value
    if resolved.id_type == "arxiv":
        return f"10.48550/arXiv.{resolved.value}"
    path = {"openalex": resolved.value, "pmid": f"pmid:{resolved.value}"}.get(resolved.id_type)
    if not path:
        return None
    client = pool.openalex
    resp = await client.get(
        f"/works/{path}",
        params=client._default_params(),
        cache_policy=("paper_metadata", 7 * 86400),
    )
    return _parse_work(resp.json()).doi or None


def _keys(doi: str, openalex_id: str) -> set[str]:
    return {k for k in (doi.lower(), openalex_id) if k}


async def fetch_citations(
    pool: ClientPool,
    doi: str,
    direction: Direction,
    limit: int,
    sources: list[str],
) -> tuple[list[Paper], dict[str, Any]]:
    """Citing works or references of ``doi``, merged across ``sources``.

    Returns (papers, meta). A failing source is reported in meta["errors"] and
    never fails the others.
    """
    meta: dict[str, Any] = {"per_source": {}}
    errors: dict[str, str] = {}
    if not is_valid_doi(doi):
        meta["errors"] = {"input": f"not a valid DOI: {doi!r}"}
        return [], meta

    papers: list[Paper] = []
    found_in: dict[str, set[str]] = {}

    totals: dict[str, int] = {}
    if "openalex" in sources:
        try:
            oa = await pool.openalex.get_citations(doi, direction, limit)
            papers.extend(oa)
            meta["per_source"]["openalex"] = len(oa)
            seed = await pool.openalex.get_work(doi)
            if seed:
                totals["openalex"] = (
                    seed.cited_by_count if direction == "citing" else len(seed.references)
                )
            for p in oa:
                for k in _keys(p.doi, p.openalex_id):
                    found_in.setdefault(k, set()).add("openalex")
        except Exception as exc:
            errors["openalex"] = f"{type(exc).__name__}: {exc}"

    links: list[CitationLink] = []
    if "opencitations" in sources:
        try:
            links = await pool.opencitations.links(doi, direction)
            totals["opencitations"] = len(links)
            known = set(found_in)
            for link in links:
                for k in _keys(link.doi, link.openalex_id):
                    found_in.setdefault(k, set()).add("opencitations")
            new = [
                link
                for link in links
                if link.doi and not (_keys(link.doi, link.openalex_id) & known)
            ]
            meta["opencitations_not_in_openalex_page"] = len(new)
            new.sort(key=lambda link: link.created, reverse=True)
            extra = await pool.opencitations.metadata([x.doi for x in new[:limit]]) if new else []
            meta["per_source"]["opencitations"] = len(extra)
            papers.extend(extra)
        except Exception as exc:
            errors["opencitations"] = f"{type(exc).__name__}: {exc}"

    self_cited = {link.doi.lower() for link in links if link.author_self_citation and link.doi}
    merged = deduplicate(papers)
    for p in merged:
        tags = set().union(*(found_in.get(k, set()) for k in _keys(p.doi, p.openalex_id)))
        if tags:
            p.external_ids["citation_sources"] = ",".join(sorted(tags))
        if p.doi.lower() in self_cited:
            p.external_ids["author_self_citation"] = "yes"
    meta["total_links"] = totals
    if errors:
        meta["errors"] = errors
    return merged, meta
