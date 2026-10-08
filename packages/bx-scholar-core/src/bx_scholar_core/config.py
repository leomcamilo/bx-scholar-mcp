"""Application settings via pydantic-settings."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REJECTED_EMAIL_PATTERNS = [
    r"@example\.(com|org|net)$",
    r"^noreply@",
    r"^no-reply@",
    r"^researcher@",
    r"^test@",
    r"^user@",
]


def find_project_root(start: Path | None = None) -> Path:
    """Locate the directory that holds ``.env`` and ``data/``.

    ``BX_SCHOLAR_HOME`` wins when set. Otherwise walk up from ``start`` (default:
    cwd) to the first directory with a ``.env`` or a uv workspace pyproject, so
    ``uv run --directory packages/<pkg>`` still finds the files at the repo root.
    Falls back to ``start`` itself (e.g. a pip install outside the repo).
    """
    home = os.environ.get("BX_SCHOLAR_HOME", "").strip()
    if home:
        return Path(home).expanduser().resolve()
    origin = (start or Path.cwd()).resolve()
    for d in (origin, *origin.parents):
        if (d / ".env").is_file():
            return d
        pyproject = d / "pyproject.toml"
        if pyproject.is_file() and "[tool.uv.workspace]" in pyproject.read_text("utf-8"):
            return d
    return origin


class Settings(BaseSettings):
    """BX-Scholar Core configuration.

    POLITE_EMAIL is required — academic APIs use it for polite rate-limit pools.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Required
    polite_email: str

    # Optional API keys
    tavily_api_key: str = ""
    s2_api_key: str = ""

    # Paths. Relative values resolve against project_root, not cwd. The bare
    # names (DATA_DIR, ...) are kept as aliases for configs written before the
    # BX_SCHOLAR_ prefix was actually honored.
    project_root: Path = Field(default_factory=find_project_root)
    data_dir: Path = Field(
        default=Path("data"), validation_alias=AliasChoices("bx_scholar_data_dir", "data_dir")
    )
    cache_dir: Path | None = Field(  # default: ~/.cache/bx-scholar/
        default=None, validation_alias=AliasChoices("bx_scholar_cache_dir", "cache_dir")
    )

    # Cache
    cache_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("bx_scholar_cache_enabled", "cache_enabled"),
    )

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
    def resolve_paths(self) -> Settings:
        self.data_dir = self._resolve(self.data_dir)
        if self.cache_dir is None:
            self.cache_dir = Path.home() / ".cache" / "bx-scholar"
        else:
            self.cache_dir = self._resolve(self.cache_dir)
        return self

    def _resolve(self, path: Path) -> Path:
        path = path.expanduser()
        return path if path.is_absolute() else (self.project_root / path).resolve()

    @property
    def user_agent(self) -> str:
        return f"BX-Scholar/0.1.0 (mailto:{self.polite_email})"


def load_settings(**overrides: object) -> Settings:
    """Load settings from environment/.env with optional overrides.

    The ``.env`` is read from the project root (see ``find_project_root``), not
    from cwd. Exits with code 1 and a clear message on validation failure.
    """
    root = find_project_root()
    overrides.setdefault("project_root", root)
    try:
        return Settings(_env_file=root / ".env", **overrides)  # type: ignore[arg-type,call-arg]
    except Exception as exc:
        print(f"[FATAL] Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
