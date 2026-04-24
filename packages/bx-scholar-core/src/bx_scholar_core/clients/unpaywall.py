"""Unpaywall API client — Open Access checking."""

from __future__ import annotations

from typing import Any

from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError


class UnpaywallClient(AsyncHTTPClient):
    """Client for the Unpaywall API.

    Rate limit: 10 req/s (generous, documented as 100K/day).
    """

    base_url = "https://api.unpaywall.org/v2"
    rate_limit = 10.0
    max_rate_period = 1.0

    def __init__(self, polite_email: str, user_agent: str = "", **kwargs) -> None:
        ua = user_agent or f"BX-Scholar/0.1.0 (mailto:{polite_email})"
        super().__init__(user_agent=ua, **kwargs)
        self._polite_email = polite_email

    async def check_oa(self, doi: str) -> dict[str, Any]:
        """Check Open Access status for a DOI.

        Returns a dict with oa_status, is_oa, pdf_url, etc.
        """
        try:
            resp = await self.get(
                f"/{doi}",
                params={"email": self._polite_email},
                cache_policy=("oa_status", 7 * 86400),
            )
            data = resp.json()

            result: dict[str, Any] = {
                "doi": doi,
                "title": data.get("title", ""),
                "oa_status": data.get("oa_status", "closed"),
                "is_oa": data.get("is_oa", False),
                "journal": data.get("journal_name", ""),
                "publisher": data.get("publisher", ""),
            }

            best_loc = data.get("best_oa_location")
            if best_loc:
                result["pdf_url"] = best_loc.get("url_for_pdf") or best_loc.get("url", "")
                result["version"] = best_loc.get("version", "unknown")
                result["license"] = best_loc.get("license") or "unknown"
                result["host_type"] = best_loc.get("host_type", "unknown")

            return result
        except NonRetryableHTTPError as exc:
            return {"doi": doi, "error": f"HTTP {exc.status_code}"}
        except Exception as exc:
            return {"doi": doi, "error": str(exc)}
