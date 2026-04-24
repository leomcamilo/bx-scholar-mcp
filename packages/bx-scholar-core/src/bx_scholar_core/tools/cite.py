"""Citation intelligence tools — influential citations, citation context, networks."""

from __future__ import annotations

import json

from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.clients.semantic_scholar import SemanticScholarClient
from bx_scholar_core.config import Settings
from bx_scholar_core.logging import get_logger

logger = get_logger(__name__)


def register_cite_tools(mcp: object, settings: Settings) -> None:
    """Register citation intelligence tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]

    @server.tool()
    async def get_influential_citations(doi_or_s2id: str, limit: int = 20) -> str:
        """Get influential citations — citations where the citing paper substantially
        engages with this work (not just incidental mentions).
        Accepts DOI or Semantic Scholar paper ID."""
        s2 = SemanticScholarClient(settings.s2_api_key, settings.user_agent)
        try:
            results = await s2.get_influential_citations(doi_or_s2id, limit)
            influential = [r for r in results if r.get("is_influential")]
            return json.dumps(
                {
                    "paper": doi_or_s2id,
                    "total_citations_returned": len(results),
                    "influential_count": len(influential),
                    "citations": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        finally:
            await s2.close()

    @server.tool()
    async def get_citation_context(citing_doi: str, cited_doi: str) -> str:
        """Get exact text snippets where one paper cites another.
        Useful for understanding HOW a paper is cited (background, method, result)."""
        s2 = SemanticScholarClient(settings.s2_api_key, settings.user_agent)
        try:
            result = await s2.get_citation_context(citing_doi, cited_doi)
            if result:
                return json.dumps(result, ensure_ascii=False, indent=2)
            return json.dumps(
                {
                    "citing_paper": citing_doi,
                    "cited_paper": cited_doi,
                    "found": False,
                    "message": "Cited paper not found in references of citing paper",
                }
            )
        finally:
            await s2.close()

    @server.tool()
    async def build_citation_network(
        seed_dois: str,
        depth: int = 1,
        max_nodes: int = 100,
    ) -> str:
        """Build a citation network from seed DOIs.
        seed_dois: comma-separated DOIs. depth: 1 or 2 levels.
        Returns nodes and edges for visualization."""

        dois = [d.strip().replace("https://doi.org/", "") for d in seed_dois.split(",")]
        depth = min(depth, 2)
        max_nodes = min(max_nodes, 200)

        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        to_process: list[tuple[str, int]] = [(doi, 0) for doi in dois]

        client = OpenAlexClient(settings.polite_email, settings.user_agent)
        try:
            while to_process and len(nodes) < max_nodes:
                doi, level = to_process.pop(0)
                if doi in nodes:
                    continue
                try:
                    paper = await client.get_work(doi)
                    if not paper:
                        continue
                    node = paper.model_dump(exclude_defaults=True)
                    node["level"] = level
                    nodes[doi] = node

                    if level < depth:
                        ref_papers = await client.get_citations(doi, "references", per_page=10)
                        for ref in ref_papers:
                            if ref.doi:
                                edges.append({"from": doi, "to": ref.doi, "type": "cites"})
                                if ref.doi not in nodes and len(nodes) < max_nodes:
                                    to_process.append((ref.doi, level + 1))
                except Exception:
                    continue
        finally:
            await client.close()

        return json.dumps(
            {
                "nodes_count": len(nodes),
                "edges_count": len(edges),
                "nodes": list(nodes.values()),
                "edges": edges[:200],
            },
            ensure_ascii=False,
            indent=2,
        )

    @server.tool()
    async def find_co_citation_clusters(
        dois: str,
        min_co_citations: int = 2,
    ) -> str:
        """Find co-citation clusters: which pairs of papers are frequently cited together.
        dois: comma-separated DOIs (max 20)."""
        doi_list = [d.strip().replace("https://doi.org/", "") for d in dois.split(",")]
        if len(doi_list) < 2:
            return json.dumps({"error": "Need at least 2 DOIs"})

        citing_sets: dict[str, set[str]] = {}
        client = OpenAlexClient(settings.polite_email, settings.user_agent)
        try:
            for doi in doi_list[:20]:
                try:
                    resp = await client.get(
                        "/works",
                        params={
                            **client._default_params(),
                            "filter": f"cites:https://doi.org/{doi}",
                            "per_page": 50,
                            "select": "id",
                        },
                    )
                    citing_sets[doi] = {w["id"] for w in resp.json().get("results", [])}
                except Exception:
                    continue
        finally:
            await client.close()

        pairs = []
        doi_keys = list(citing_sets.keys())
        for i in range(len(doi_keys)):
            for j in range(i + 1, len(doi_keys)):
                shared = citing_sets[doi_keys[i]] & citing_sets[doi_keys[j]]
                if len(shared) >= min_co_citations:
                    pairs.append(
                        {
                            "paper_a": doi_keys[i],
                            "paper_b": doi_keys[j],
                            "co_citations": len(shared),
                        }
                    )
        pairs.sort(key=lambda x: x["co_citations"], reverse=True)
        return json.dumps(
            {"co_citation_pairs": pairs[:50], "total_pairs": len(pairs)},
            ensure_ascii=False,
            indent=2,
        )
