"""Application settings via pydantic-settings."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REJECTED_EMAIL_PATTERNS = [
    r"@example\.(com|org|net)$",
    r"^noreply@",
    r"^no-reply@",
    r"^researcher@",
    r"^test@",
    r"^user@",
]


class Settings(BaseSettings):
    """BX-Scholar Core configuration.

    POLITE_EMAIL is required — academic APIs use it for polite rate-limit pools.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required
    polite_email: str

    # Optional API keys
    tavily_api_key: str = ""
    s2_api_key: str = ""

    # Paths
    data_dir: Path = Path("data")
    cache_dir: Path | None = None  # default: ~/.cache/bx-scholar/

    # Cache
    cache_enabled: bool = True

    # Logging
    log_level: str = "INFO"
    log_format: str = "console"  # "console" or "json"

    @field_validator("polite_email")
    @classmethod
    def validate_polite_email(cls, v: str) -> str:
        v = v.strip()
        if not v:
            msg = (
                "POLITE_EMAIL is required. "
                "Academic APIs (OpenAlex, CrossRef, Unpaywall) use this for polite rate-limit pools. "
                "Set it in .env or as an environment variable."
            )
            raise ValueError(msg)
        if "@" not in v:
            raise ValueError(f"POLITE_EMAIL must be a valid email address, got: {v!r}")
        for pattern in _REJECTED_EMAIL_PATTERNS:
            if re.search(pattern, v, re.IGNORECASE):
                raise ValueError(
                    f"POLITE_EMAIL must be a real email address, not a placeholder. Got: {v!r}"
                )
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got: {v!r}")
        return v

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        allowed = {"console", "json"}
        v = v.lower()
        if v not in allowed:
            raise ValueError(f"log_format must be one of {allowed}, got: {v!r}")
        return v

    @model_validator(mode="after")
    def set_default_cache_dir(self) -> Settings:
        if self.cache_dir is None:
            self.cache_dir = Path.home() / ".cache" / "bx-scholar"
        return self

    @property
    def user_agent(self) -> str:
        return f"BX-Scholar/0.1.0 (mailto:{self.polite_email})"


def load_settings(**overrides: object) -> Settings:
    """Load settings from environment/.env with optional overrides.

    Exits with code 1 and a clear message on validation failure.
    """
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except Exception as exc:
        print(f"[FATAL] Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
