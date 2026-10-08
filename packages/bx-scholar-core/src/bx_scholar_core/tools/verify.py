"""Verification tools — citation verification, retraction checking."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from bx_scholar_core.clients.crossref import CrossRefClient
from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.paper import Paper
from bx_scholar_core.verification.decide import Decision, Query, decide
from bx_scholar_core.verification.titles import TitleMode

if TYPE_CHECKING:
    from bx_scholar_core.clients.pool import ClientPool

logger = get_logger(__name__)

CANDIDATES_PER_SOURCE = 10


async def _retrieve(
    cr: CrossRefClient, oa: OpenAlexClient, query: Query
) -> tuple[list[tuple[str, Paper]], dict[str, str]]:
    """Candidates from both sources, kept in order and undecided. No year filter."""
    text = f"{query.author} {query.title}".strip()
    results = await asyncio.gather(
        cr.search_bibliographic(text, rows=CANDIDATES_PER_SOURCE),
        oa.search(query.title, per_page=CANDIDATES_PER_SOURCE),
        return_exceptions=True,
    )
    found: list[tuple[str, Paper]] = []
    errors: dict[str, str] = {}
    cr_result, oa_result = results
    for source, result in (("crossref", cr_result), ("openalex", oa_result)):
        if isinstance(result, BaseException):
            errors[source] = f"{type(result).__name__}: {result}"
            logger.warning("verify_source_failed", source=source, error=str(result))
    if isinstance(cr_result, list):
        found.extend(("crossref", p) for p in cr_result)
    if isinstance(oa_result, tuple):
        found.extend(("openalex", p) for p in oa_result[0])
    return found, errors


def _response(query: Query, decision: Decision, errors: dict[str, str]) -> dict[str, Any]:
    q = {"author": query.author, "year": query.year, "title": query.title}
    best = decision.best
    if decision.status == "verified" and best:
        out: dict[str, Any] = {
            "verified": True,
            "status": "verified",
            "source": ",".join(best.sources),
            "confidence": best.confidence,
            "checks": best.checks(),
            "match": best.paper.model_dump(exclude_defaults=True),
        }
        if best.warnings():
            out["warnings"] = best.warnings()
        if errors:
            out["source_errors"] = errors  # verified by one source; the other failed
        return out

    messages = {
        "conflict": "A work was identified, but it contradicts the citation (see rejected_because).",
        "ambiguous": "More than one work matches the citation; it cannot be confirmed as a single work.",
        "insufficient": "The citation could not be confirmed with the information available.",
    }
    out = {
        "verified": False,
        "status": decision.status,
        "confidence": best.confidence if best else "none",
        "query": q,
        "message": messages[decision.status],
        "next_action": decision.next_action,
    }
    if errors:
        out["source_errors"] = errors
        if decision.best is None:
            out["message"] = "Sources failed; the citation was not checked. Try again later."
            out["next_action"] = (
                "Retry; a source error is not evidence that the work does not exist."
            )
    if best:
        out["closest_match"] = {
            "source": ",".join(best.sources),
            "checks": best.checks(),
            "rejected_because": best.reasons(),
            "match": best.paper.model_dump(exclude_defaults=True),
        }
    if decision.status == "ambiguous":
        out["candidates"] = [
            {
                "source": ",".join(e.sources),
                "doi": e.paper.doi,
                "title": e.paper.title,
                "year": e.paper.year,
            }
            for e in decision.verifiable
        ]
    return out


async def _verify_one(
    cr: CrossRefClient,
    oa: OpenAlexClient,
    author: str,
    year: int | None,
    title: str,
    title_mode: TitleMode = "auto",
) -> dict[str, Any]:
    query = Query(author=author, year=year, title=title, title_mode=title_mode)
    found, errors = await _retrieve(cr, oa, query)
    return _response(query, decide(query, found), errors)


def register_verify_tools(mcp: object, pool: ClientPool) -> None:
    """Register citation verification tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]

    @server.tool(structured_output=False)
    async def verify_citation(
        author: str,
        year: int,
        title_fragment: str,
        title_mode: TitleMode = "auto",
    ) -> str:
        """Verify that a cited work exists as cited. Anti-hallucination tool.

        Searches Crossref and OpenAlex and confirms only when exactly one work
        matches: the title (full title, main title before the subtitle, or a
        literal contiguous passage of at least 4 content words, same word order),
        every cited author (surname equal; given names or initials compatible), and
        the year (±1, which lowers confidence to medium).
        author: one or more names ("Silva, L.; Souza, A." or "Mergel et al.").
        title_mode: "full" if title_fragment is the complete title (a different
        title then counts as a conflict), "fragment" for a passage, "auto" (default).

        status: verified | conflict (a work was identified and contradicts the
        citation) | ambiguous (several works match) | insufficient (not enough to
        confirm: partial title, missing metadata, nothing found). When not
        verified, closest_match, rejected_because and next_action say why and what
        to try next. Not finding a work is not proof that it does not exist."""
        result = await _verify_one(
            pool.crossref, pool.openalex, author, year, title_fragment, title_mode
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    @server.tool(structured_output=False)
    async def check_retraction(doi: str) -> str:
        """Check if a paper has been retracted. Always verify before citing."""
        doi = doi.strip().replace("https://doi.org/", "")
        status = await pool.crossref.check_retraction(doi)
        return json.dumps(status.model_dump(), ensure_ascii=False, indent=2)

    @server.tool(structured_output=False)
    async def batch_verify_references(references_json: str) -> str:
        """Verify a batch of references (up to 30), with the same rules as verify_citation.
        Input: JSON array of {"author": "...", "year": 2020, "title": "...",
        "title_mode": "auto|full|fragment" (optional)}.
        Returns counts per status and, for each reference, status, DOI and reasons."""
        try:
            refs = json.loads(references_json)
        except json.JSONDecodeError:
            return json.dumps(
                {"error": "Invalid JSON. Expected array of {author, year, title} objects."}
            )

        results = []
        for ref in refs[:30]:
            year = ref.get("year")
            try:
                year = int(year) if year else None
            except (TypeError, ValueError):
                year = None
            mode = ref.get("title_mode", "auto")
            if mode not in ("auto", "full", "fragment"):
                mode = "auto"
            r = await _verify_one(
                pool.crossref,
                pool.openalex,
                str(ref.get("author", "")),
                year,
                str(ref.get("title", "")),
                mode,
            )
            m = r.get("match") or (r.get("closest_match") or {}).get("match") or {}
            entry: dict[str, Any] = {
                "query": ref,
                "verified": r["verified"],
                "status": r["status"],
                "confidence": r["confidence"],
                "doi": m.get("doi", "") if r["verified"] else "",
                "matched_title": m.get("title", ""),
            }
            if r["verified"]:
                entry["checks"] = r["checks"]
            elif "closest_match" in r:
                entry["checks"] = r["closest_match"]["checks"]
                entry["rejected_because"] = r["closest_match"]["rejected_because"]
                entry["next_action"] = r["next_action"]
            results.append(entry)

        counts = {s: sum(1 for r in results if r["status"] == s) for s in
                  ("verified", "conflict", "ambiguous", "insufficient")}  # fmt: skip
        return json.dumps(
            {
                "total": len(results),
                "verified": counts["verified"],
                "unverified": len(results) - counts["verified"],
                "by_status": counts,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
