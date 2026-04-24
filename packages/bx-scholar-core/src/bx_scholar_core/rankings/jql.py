"""JQL (Harzing's Journal Quality List) loader — CSV, no pandas."""

from __future__ import annotations

import csv
from pathlib import Path

from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.ranking import JQLEntry

logger = get_logger(__name__)


def load_jql(path: Path) -> tuple[dict[str, JQLEntry], dict[str, str]]:
    """Load JQL rankings from CSV.

    Returns (issn_index, name_index) where name_index maps lowercase title -> issn.
    """
    issn_index: dict[str, JQLEntry] = {}
    name_index: dict[str, str] = {}

    if not path.exists():
        logger.warning("jql_not_found", path=str(path))
        return issn_index, name_index

    try:
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                issn = row.get("issn", "").strip()
                if not issn:
                    continue
                entry = JQLEntry(
                    title=row.get("journal", ""),
                    subject=row.get("subject", ""),
                    ft50=row.get("ft2016", ""),
                    cnrs=row.get("cnrs2020", ""),
                    hceres=row.get("hceres2021", ""),
                    abs=row.get("ajg_abs2024", ""),
                    abdc=row.get("abdc2025", ""),
                    fnege=row.get("fnege2025", ""),
                    vhb=row.get("vhb2024", ""),
                    scopus_citescore=row.get("scopus2024", ""),
                )
                issn_index[issn] = entry
                title = row.get("journal", "").strip()
                if title:
                    name_index[title.lower()] = issn

        logger.info("jql_loaded", count=len(issn_index))
    except Exception as exc:
        logger.error("jql_load_failed", error=str(exc))

    return issn_index, name_index
