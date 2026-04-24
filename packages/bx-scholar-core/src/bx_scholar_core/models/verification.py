"""Models for citation verification and retraction checking."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    """Result of verifying a single citation."""

    verified: bool
    source: str = ""  # "crossref", "openalex"
    confidence: Literal["high", "medium", "low", "none"] = "none"
    query: dict[str, str | int] = Field(default_factory=dict)
    match: dict[str, object] | None = None
    message: str = ""


class RetractionStatus(BaseModel):
    """Result of checking whether a paper has been retracted."""

    doi: str
    retracted: bool = False
    is_retraction_notice: bool = False
    title: str = ""
    updates: list[dict[str, str]] = Field(default_factory=list)
    error: str = ""
