"""Verification tools — citation verification, retraction checking."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from bx_scholar_core.citation_match import MAX_YEAR_DELTA, CitationMatch, best_match
from bx_scholar_core.clients.crossref import CrossRefClient
from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.logging import get_logger

if TYPE_CHECKING:
    from bx_scholar_core.clients.pool import ClientPool

logger = get_logger(__name__)


async def _verify_one(
    cr: CrossRefClient,
    oa: OpenAlexClient,
    author: str,
    year: int | None,
    title_fragment: str,
) -> dict[str, Any]:
    """CrossRef first, OpenAlex only when CrossRef has no verified hit."""
    candidates: list[tuple[str, CitationMatch]] = []

    cr_match = await cr.find_citation(author, year, title_fragment)
    if cr_match is not None:
        candidates.append(("crossref", cr_match))

    if cr_match is None or not cr_match.verified:
        papers, _ = await oa.search(
            f"{author} {title_fragment}",
            year_from=year - MAX_YEAR_DELTA if year else None,
            year_to=year + MAX_YEAR_DELTA if year else None,
            sort="relevance_score:desc",
            per_page=5,
        )
        oa_match = best_match(papers, author, year, title_fragment)
        if oa_match is not None:
            candidates.append(("openalex", oa_match))

    query = {"author": author, "year": year, "title": title_fragment}
    if not candidates:
        return {
            "verified": False,
            "confidence": "none",
            "query": query,
            "message": "No candidate found in CrossRef or OpenAlex. This citation may be fabricated.",
        }

    source, best = max(candidates, key=lambda c: c[1]._rank())
    match = best.paper.model_dump(exclude_defaults=True)
    if best.verified:
        result: dict[str, Any] = {
            "verified": True,
            "source": source,
            "confidence": best.confidence,
            "checks": best.checks(),
            "match": match,
        }
        if best.confidence != "high":
            result["warnings"] = best.reasons()
        return result

    return {
        "verified": False,
        "confidence": best.confidence,
        "query": query,
        "message": (
            "Search returned papers, but none matches title, author and year. "
            "This citation may be fabricated or mis-attributed."
        ),
        "closest_match": {
            "source": source,
            "checks": best.checks(),
            "rejected_because": best.reasons(),
            "match": match,
        },
    }


def register_verify_tools(mcp: object, pool: ClientPool) -> None:
    """Register citation verification tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]

    @server.tool(structured_output=False)
    async def verify_citation(
        author: str,
        year: int,
        title_fragment: str,
    ) -> str:
        """Verify if a citation exists. Anti-hallucination tool.
        Checks CrossRef first, falls back to OpenAlex. A hit only counts when the
        title fragment, the cited author and the year (±1) all agree with it.
        Returns verified status, confidence, per-field checks and the match; when
        unverified, the closest candidate and why it was rejected."""
        cr = pool.crossref
        oa = pool.openalex
        result = await _verify_one(cr, oa, author, year, title_fragment)
        return json.dumps(result, ensure_ascii=False, indent=2)

    @server.tool(structured_output=False)
    async def check_retraction(doi: str) -> str:
        """Check if a paper has been retracted. Always verify before citing."""
        doi = doi.strip().replace("https://doi.org/", "")
        cr = pool.crossref
        status = await cr.check_retraction(doi)
        return json.dumps(status.model_dump(), ensure_ascii=False, indent=2)

    @server.tool(structured_output=False)
    async def batch_verify_references(references_json: str) -> str:
        """Verify a batch of references (up to 30). Anti-hallucination tool.
        Input: JSON array of {"author": "...", "year": 2020, "title": "key words"}.
        Returns verified/unverified counts and per-reference status."""
        try:
            refs = json.loads(references_json)
        except json.JSONDecodeError:
            return json.dumps(
                {"error": "Invalid JSON. Expected array of {author, year, title} objects."}
            )

        cr = pool.crossref
        oa = pool.openalex
        results = []

        for ref in refs[:30]:
            year = ref.get("year")
            try:
                year = int(year) if year else None
            except (TypeError, ValueError):
                year = None
            r = await _verify_one(
                cr, oa, str(ref.get("author", "")), year, str(ref.get("title", ""))
            )
            m = r.get("match") or (r.get("closest_match") or {}).get("match") or {}
            entry = {
                "query": ref,
                "verified": r["verified"],
                "confidence": r["confidence"],
                "source": r.get("source", ""),
                "doi": m.get("doi", "") if r["verified"] else "",
                "matched_title": m.get("title", ""),
            }
            if r["verified"]:
                entry["checks"] = r["checks"]
            elif "closest_match" in r:
                entry["checks"] = r["closest_match"]["checks"]
                entry["rejected_because"] = r["closest_match"]["rejected_because"]
            results.append(entry)

        verified_count = sum(1 for r in results if r.get("verified"))
        return json.dumps(
            {
                "total": len(results),
                "verified": verified_count,
                "unverified": len(results) - verified_count,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
