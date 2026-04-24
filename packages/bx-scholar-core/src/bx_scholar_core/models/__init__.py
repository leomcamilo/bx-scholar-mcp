"""Canonical data models for academic entities."""

from __future__ import annotations

from bx_scholar_core.models.paper import Author, Paper, Venue
from bx_scholar_core.models.ranking import JournalMetrics, RankingEntry
from bx_scholar_core.models.verification import RetractionStatus, VerificationResult

__all__ = [
    "Author",
    "JournalMetrics",
    "Paper",
    "RankingEntry",
    "RetractionStatus",
    "Venue",
    "VerificationResult",
]
