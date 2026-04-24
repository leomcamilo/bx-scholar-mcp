"""Models for journal ranking data (SJR, Qualis CAPES, JQL)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RankingEntry(BaseModel):
    """A single ranking from one system."""

    source: str  # "sjr", "qualis", "jql_abs", "jql_abdc", etc.
    value: str  # "Q1", "A1", "4*", "A+", etc.
    tier: str = ""  # normalized tier: "S", "A", "B", "C", "D", "grey"
    metadata: dict[str, str] = Field(default_factory=dict)


class SJREntry(BaseModel):
    """SJR ranking data for a journal."""

    title: str = ""
    sjr_score: str = ""
    quartile: str = ""  # Q1, Q2, Q3, Q4
    h_index: str = ""
    country: str = ""
    area: str = ""
    type: str = ""
    publisher: str = ""


class QualisEntry(BaseModel):
    """Qualis CAPES classification for a journal."""

    title: str = ""
    classification: str = ""  # A1, A2, A3, A4, B1, B2, B3, B4, C
    area: str = ""


class JQLEntry(BaseModel):
    """Harzing's JQL rankings for a journal."""

    title: str = ""
    subject: str = ""
    ft50: str = ""
    abs: str = ""  # ABS/AJG (UK)
    abdc: str = ""  # ABDC (Australia)
    cnrs: str = ""  # CNRS (France)
    fnege: str = ""  # FNEGE (France)
    vhb: str = ""  # VHB (Germany)
    hceres: str = ""
    scopus_citescore: str = ""


class JournalMetrics(BaseModel):
    """Combined journal metrics across all ranking systems."""

    issn: str
    title: str = ""
    sjr: SJREntry | None = None
    qualis: QualisEntry | None = None
    jql: JQLEntry | None = None

    @property
    def best_tier(self) -> str:
        """Return the best tier across all ranking systems.

        Priority: S > A > B > C > D > grey > unknown.
        """
        tiers: list[str] = []
        if self.sjr and self.sjr.quartile:
            tier_map = {"Q1": "A", "Q2": "B", "Q3": "C", "Q4": "D"}
            tiers.append(tier_map.get(self.sjr.quartile, "D"))
        if self.qualis and self.qualis.classification:
            q = self.qualis.classification.upper()
            if q in ("A1", "A2"):
                tiers.append("A")
            elif q in ("A3", "A4"):
                tiers.append("B")
            elif q in ("B1", "B2"):
                tiers.append("C")
            else:
                tiers.append("D")
        if self.jql and self.jql.abs:
            abs_map = {"4*": "S", "4": "A", "3": "B", "2": "C", "1": "D"}
            tiers.append(abs_map.get(self.jql.abs, "D"))
        if not tiers:
            return "unknown"
        priority = ["S", "A", "B", "C", "D"]
        return min(tiers, key=lambda t: priority.index(t) if t in priority else 99)
