"""RankingService — unified lookup across SJR, Qualis, JQL."""

from __future__ import annotations

from pathlib import Path

from rapidfuzz import fuzz

from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.ranking import JournalMetrics, JQLEntry, QualisEntry, SJREntry
from bx_scholar_core.rankings.jql import load_jql
from bx_scholar_core.rankings.qualis import load_qualis
from bx_scholar_core.rankings.sjr import load_sjr

logger = get_logger(__name__)


def _normalize_issn(issn: str) -> str:
    issn = issn.strip().upper()
    if len(issn) == 8 and "-" not in issn:
        issn = f"{issn[:4]}-{issn[4:]}"
    return issn


class RankingService:
    """Unified journal ranking lookup across SJR, Qualis CAPES, and JQL."""

    def __init__(self, data_dir: Path) -> None:
        self._sjr_index: dict[str, SJREntry] = {}
        self._sjr_by_name: dict[str, str] = {}
        self._qualis_index: dict[str, QualisEntry] = {}
        self._jql_index: dict[str, JQLEntry] = {}
        self._jql_by_name: dict[str, str] = {}
        self._data_dir = data_dir

    def load(self) -> None:
        """Load all ranking data from files in data_dir."""
        self._sjr_index, self._sjr_by_name = load_sjr(self._data_dir / "sjr_rankings.csv")
        self._qualis_index = load_qualis(self._data_dir / "qualis_capes.xlsx")
        self._jql_index, self._jql_by_name = load_jql(self._data_dir / "jql_rankings.csv")
        logger.info(
            "rankings_loaded",
            sjr=len(self._sjr_index),
            qualis=len(self._qualis_index),
            jql=len(self._jql_index),
        )

    def lookup(self, issn_or_name: str) -> JournalMetrics:
        """Look up a journal by ISSN or name.

        For name lookups, uses exact match first, then fuzzy match (>85%).
        """
        query = issn_or_name.strip()

        # Try ISSN lookup
        normalized = _normalize_issn(query)
        sjr = self._sjr_index.get(normalized)
        qualis = self._qualis_index.get(normalized)
        jql = self._jql_index.get(normalized)

        if sjr or qualis or jql:
            title = (sjr and sjr.title) or (jql and jql.title) or (qualis and qualis.title) or ""
            return JournalMetrics(issn=normalized, title=title, sjr=sjr, qualis=qualis, jql=jql)

        # Try name lookup (exact)
        name_lower = query.lower()
        found_issn = self._sjr_by_name.get(name_lower) or self._jql_by_name.get(name_lower)
        if found_issn:
            found_issn = _normalize_issn(found_issn)
            return JournalMetrics(
                issn=found_issn,
                title=query,
                sjr=self._sjr_index.get(found_issn),
                qualis=self._qualis_index.get(found_issn),
                jql=self._jql_index.get(found_issn),
            )

        # Try fuzzy name match
        best_issn = self._fuzzy_match(name_lower)
        if best_issn:
            best_issn = _normalize_issn(best_issn)
            sjr = self._sjr_index.get(best_issn)
            jql = self._jql_index.get(best_issn)
            title = (sjr and sjr.title) or (jql and jql.title) or ""
            return JournalMetrics(
                issn=best_issn,
                title=title,
                sjr=sjr,
                qualis=self._qualis_index.get(best_issn),
                jql=jql,
            )

        return JournalMetrics(issn=query)

    def _fuzzy_match(self, name_lower: str) -> str | None:
        """Find best fuzzy match across SJR and JQL name indexes."""
        best_score = 0.0
        best_issn: str | None = None

        for title, issn in self._sjr_by_name.items():
            score = fuzz.ratio(name_lower, title)
            if score > best_score:
                best_score = score
                best_issn = issn

        for title, issn in self._jql_by_name.items():
            score = fuzz.ratio(name_lower, title)
            if score > best_score:
                best_score = score
                best_issn = issn

        if best_score >= 85:
            return best_issn
        return None

    def top_journals_for_field(self, field: str, limit: int = 20) -> list[JournalMetrics]:
        """Get top journals in a field sorted by SJR score."""
        field_lower = field.lower()
        matches: list[tuple[float, str, SJREntry]] = []

        for issn, entry in self._sjr_index.items():
            area = str(entry.area).lower()
            title = str(entry.title).lower()
            if field_lower in area or field_lower in title:
                try:
                    sjr_val = float(str(entry.sjr_score).replace(",", "."))
                except (ValueError, TypeError):
                    sjr_val = 0.0
                matches.append((sjr_val, issn, entry))

        matches.sort(key=lambda x: x[0], reverse=True)

        results: list[JournalMetrics] = []
        for _, issn, sjr_entry in matches[:limit]:
            results.append(
                JournalMetrics(
                    issn=issn,
                    title=sjr_entry.title,
                    sjr=sjr_entry,
                    qualis=self._qualis_index.get(issn),
                    jql=self._jql_index.get(issn),
                )
            )
        return results
