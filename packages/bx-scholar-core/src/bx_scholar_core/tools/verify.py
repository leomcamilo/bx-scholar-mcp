"""Verification tools — citation verification, retraction checking."""

from __future__ import annotations

import asyncio
import json

from bx_scholar_core.clients.crossref import CrossRefClient
from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.config import Settings
from bx_scholar_core.logging import get_logger

logger = get_logger(__name__)


def register_verify_tools(mcp: object, settings: Settings) -> None:
    """Register citation verification tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]

    @server.tool()
    async def verify_citation(
        author: str,
        year: int,
        title_fragment: str,
    ) -> str:
        """Verify if a citation exists. Anti-hallucination tool.
        Checks CrossRef first, falls back to OpenAlex.
        Returns verified status, confidence level, and closest match."""
        cr = CrossRefClient(settings.polite_email, settings.user_agent)
        try:
            verified, match = await cr.verify_citation(author, year, title_fragment)
            if verified and match:
                return json.dumps(
                    {
                        "verified": True,
                        "source": "crossref",
                        "confidence": "high",
                        "match": match.model_dump(exclude_defaults=True),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
        finally:
            await cr.close()

        # Fallback: OpenAlex
        oa = OpenAlexClient(settings.polite_email, settings.user_agent)
        try:
            papers, _ = await oa.search(
                f"{author} {title_fragment}",
                year_from=year,
                year_to=year,
                per_page=5,
            )
            if papers:
                return json.dumps(
                    {
                        "verified": True,
                        "source": "openalex",
                        "confidence": "medium",
                        "match": papers[0].model_dump(exclude_defaults=True),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
        finally:
            await oa.close()

        return json.dumps(
            {
                "verified": False,
                "query": {"author": author, "year": year, "title": title_fragment},
                "message": "No match found in CrossRef or OpenAlex. This citation may be fabricated.",
            }
        )

    @server.tool()
    async def check_retraction(doi: str) -> str:
        """Check if a paper has been retracted. Always verify before citing."""
        doi = doi.strip().replace("https://doi.org/", "")
        cr = CrossRefClient(settings.polite_email, settings.user_agent)
        try:
            status = await cr.check_retraction(doi)
            return json.dumps(status.model_dump(), ensure_ascii=False, indent=2)
        finally:
            await cr.close()

    @server.tool()
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

        cr = CrossRefClient(settings.polite_email, settings.user_agent)
        results = []

        try:
            for ref in refs[:30]:
                author = ref.get("author", "")
                year = ref.get("year", 2000)
                title = ref.get("title", "")
                verified, match = await cr.verify_citation(author, year, title)
                results.append(
                    {
                        "query": ref,
                        "verified": verified,
                        "doi": match.doi if match and verified else "",
                        "matched_title": match.title if match else "",
                    }
                )
                await asyncio.sleep(0.1)  # gentle rate limiting
        finally:
            await cr.close()

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
