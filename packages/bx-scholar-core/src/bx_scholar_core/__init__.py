"""BX-Scholar Core — Academic research MCP tools.

Public API for use as a Python library:

    from bx_scholar_core import Paper, Author, CacheStore, create_server
"""

from __future__ import annotations

from bx_scholar_core.cache import CacheStore
from bx_scholar_core.config import Settings, load_settings
from bx_scholar_core.models import (
    Author,
    JournalMetrics,
    Paper,
    RankingEntry,
    RetractionStatus,
    Venue,
    VerificationResult,
)
from bx_scholar_core.server import create_server

__version__ = "0.1.0"

__all__ = [
    "Author",
    "CacheStore",
    "JournalMetrics",
    "Paper",
    "RankingEntry",
    "RetractionStatus",
    "Settings",
    "Venue",
    "VerificationResult",
    "__version__",
    "create_server",
    "load_settings",
]
