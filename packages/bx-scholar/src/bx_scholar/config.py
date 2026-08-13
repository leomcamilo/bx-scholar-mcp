"""Configuração do BX-Scholar v2.

Herda o contrato do core (``POLITE_EMAIL`` obrigatório, chaves opcionais) e
acrescenta o que é novo no v2: a URL do store durável e os tetos de projeção.

Nomenclatura: o core usa ``env_prefix=""``, então ``POLITE_EMAIL``/``CACHE_DIR``
seguem sem prefixo por compatibilidade com ``/etc/bx-scholar.env``. As chaves
novas do v2 usam o prefixo explícito ``BX_SCHOLAR_`` para não colidir com nada.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Alvo default do store durável. SQLite é o alvo intermediário: o schema é
# dialeto-agnóstico, então migrar para Postgres é trocar esta URL por
# postgresql+asyncpg://... e rodar `alembic upgrade head`.
_DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "bx-scholar"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- herdado do core -------------------------------------------------
    polite_email: str
    tavily_api_key: str = ""
    s2_api_key: str = ""
    data_dir: Path = Path("data")
    cache_dir: Path | None = None
    cache_enabled: bool = True
    log_level: str = "INFO"
    log_format: str = "console"

    # --- store durável ---------------------------------------------------
    # Vazio => SQLite em ~/.local/share/bx-scholar/bx_scholar.db
    database_url: str = Field(default="", alias="BX_SCHOLAR_DATABASE_URL")

    # --- tetos de projeção -----------------------------------------------
    # O que volta para o modelo é uma PROJEÇÃO, nunca o pack inteiro. O teto
    # existe porque o BXat corta o payload agregado de sources em 600k chars
    # (BXAT_SOURCES_PAYLOAD_MAX_CHARS) e porque cada char aqui é token no prompt.
    projection_max_chars: int = Field(default=12000, alias="BX_SCHOLAR_PROJECTION_MAX_CHARS")
    projection_max_works: int = Field(default=25, alias="BX_SCHOLAR_PROJECTION_MAX_WORKS")

    # --- fan-out ----------------------------------------------------------
    # Teto por conector. Uma fonte lenta não pode paralisar a pesquisa nem
    # produzir falsa impressão de completude — ela vira "timeout_partial" no
    # bloco coverage da resposta.
    connector_timeout_quick: float = Field(default=6.0, alias="BX_SCHOLAR_TIMEOUT_QUICK")
    connector_timeout_balanced: float = Field(default=12.0, alias="BX_SCHOLAR_TIMEOUT_BALANCED")
    connector_timeout_deep: float = Field(default=45.0, alias="BX_SCHOLAR_TIMEOUT_DEEP")

    @field_validator("polite_email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = v.strip()
        if not v or "@" not in v:
            msg = (
                "POLITE_EMAIL é obrigatório e precisa ser um e-mail real — "
                "OpenAlex, CrossRef, Unpaywall e Europe PMC usam isso para o "
                "polite pool (limites maiores e contato em caso de abuso)."
            )
            raise ValueError(msg)
        return v

    @field_validator("log_level")
    @classmethod
    def _validate_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"log_level deve ser um de {allowed}, veio: {v!r}")
        return v

    @field_validator("log_format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        allowed = {"console", "json"}
        v = v.lower()
        if v not in allowed:
            raise ValueError(f"log_format deve ser um de {allowed}, veio: {v!r}")
        return v

    @model_validator(mode="after")
    def _defaults(self) -> Settings:
        if self.cache_dir is None:
            self.cache_dir = Path.home() / ".cache" / "bx-scholar"
        if not self.database_url:
            _DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
            self.database_url = f"sqlite+aiosqlite:///{_DEFAULT_DB_DIR / 'bx_scholar.db'}"
        return self

    @property
    def user_agent(self) -> str:
        from bx_scholar import __version__

        return f"BX-Scholar/{__version__} (mailto:{self.polite_email})"

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    def timeout_for(self, mode: str) -> float:
        return {
            "quick": self.connector_timeout_quick,
            "balanced": self.connector_timeout_balanced,
            "deep": self.connector_timeout_deep,
        }.get(mode, self.connector_timeout_balanced)


def load_settings(**overrides: object) -> Settings:
    """Carrega settings do ambiente/.env. Sai com código 1 e mensagem clara."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except Exception as exc:
        print(f"[FATAL] Erro de configuração: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
