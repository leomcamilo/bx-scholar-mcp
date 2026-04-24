"""SJR rankings loader — CSV with semicolon separator, no pandas."""

from __future__ import annotations

import csv
from pathlib import Path

from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.ranking import SJREntry

logger = get_logger(__name__)


def _normalize_issn(raw: str) -> str:
    """Normalize ISSN: strip, add hyphen if needed."""
    raw = raw.strip()
    if len(raw) == 8 and "-" not in raw:
        return f"{raw[:4]}-{raw[4:]}"
    return raw


def load_sjr(path: Path) -> tuple[dict[str, SJREntry], dict[str, str]]:
    """Load SJR rankings from CSV.

    Returns (issn_index, name_index) where name_index maps lowercase title -> issn.
    """
    issn_index: dict[str, SJREntry] = {}
    name_index: dict[str, str] = {}

    if not path.exists():
        logger.warning("sjr_not_found", path=str(path))
        return issn_index, name_index

    try:
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                issn_raw = row.get("Issn", "").strip()
                title = row.get("Title", "").strip()
                if not issn_raw:
                    continue

                entry = SJREntry(
                    title=title,
                    sjr_score=row.get("SJR", ""),
                    quartile=row.get("SJR Best Quartile", ""),
                    h_index=row.get("H index", ""),
                    country=row.get("Country", ""),
                    area=row.get("Areas", ""),
                    type=row.get("Type", ""),
                    publisher=row.get("Publisher", ""),
                )

                for single in issn_raw.split(","):
                    normalized = _normalize_issn(single)
                    if normalized:
                        issn_index[normalized] = entry

                if title:
                    first_issn = _normalize_issn(issn_raw.split(",")[0])
                    name_index[title.lower()] = first_issn

        logger.info("sjr_loaded", count=len(issn_index))
    except Exception as exc:
        logger.error("sjr_load_failed", error=str(exc))

    return issn_index, name_index
