"""Contrato declarado por conector: orçamento, timeout e circuit breaker.

O v1 já tinha rate limit (``aiolimiter``) e retry (``tenacity``) no
``AsyncHTTPClient``, e isso é bom. O que faltava era **circuit breaker**: quando
uma fonte está fora do ar ou estrangulando, cada requisição ainda pagava
timeout + 3 tentativas com backoff exponencial antes de desistir. Numa busca com
fan-out sobre 6 fontes, uma fonte doente arrastava a resposta inteira para o
pior caso — que é exatamente o que mata UX de chat.

Com breaker, a segunda requisição para uma fonte que acabou de falhar N vezes
retorna **imediatamente**, e o fan-out registra ``unavailable`` no bloco
``coverage`` em vez de esperar. O usuário vê o que faltou, na hora, em vez de
esperar 40 s por uma resposta silenciosamente incompleta.

Os limites vivem aqui e não no orquestrador porque são fato sobre a fonte, não
política da busca:

- PubMed/E-utilities: 3 req/s sem chave, 10 com chave (NCBI).
- CrossRef: pool público aberto; o polite pool (com ``mailto``) dá limites e
  concorrência maiores, e a documentação pede cache e backoff.
- OpenAlex: sem autenticação, polite pool por ``mailto``, com cota diária.
- Semantic Scholar: pool anônimo compartilhado e sujeito a throttling; a chave
  inicial tem limite próprio da ordem de 1 rps.

Esses números mudam com o tempo. O ponto do módulo é que mudem **em um lugar**.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

from bx_scholar_core.logging import get_logger

logger = get_logger(__name__)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Requisição recusada localmente: o circuito da fonte está aberto.

    Não é erro de rede — é a decisão de não gastar tempo com uma fonte que
    acabou de falhar repetidamente. O fan-out traduz isso em ``unavailable``.
    """

    def __init__(self, name: str, retry_in: float) -> None:
        self.name = name
        self.retry_in = retry_in
        super().__init__(f"circuito aberto para {name}; nova tentativa em {retry_in:.1f}s")


@dataclass
class CircuitBreaker:
    """Breaker por conector, com meia-abertura para sondagem.

    ``failure_threshold`` falhas consecutivas abrem o circuito por
    ``cooldown``. Passado o cooldown, o estado vira ``half_open`` e UMA
    requisição passa como sonda: se der certo, fecha; se falhar, reabre com o
    cooldown reiniciado. Sucesso em estado fechado zera o contador — só falha
    *consecutiva* conta, senão um serviço saudável com erro esporádico acabaria
    aberto depois de horas de operação normal.
    """

    name: str
    failure_threshold: int = 5
    cooldown: float = 60.0

    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    _half_open_in_flight: bool = field(default=False, repr=False)

    def allow(self) -> None:
        """Levanta ``CircuitOpenError`` se a requisição não deve sair."""
        if self.state is CircuitState.CLOSED:
            return

        elapsed = time.monotonic() - self.opened_at
        if self.state is CircuitState.OPEN:
            if elapsed < self.cooldown:
                raise CircuitOpenError(self.name, self.cooldown - elapsed)
            self.state = CircuitState.HALF_OPEN
            self._half_open_in_flight = False
            logger.info("circuit_half_open", connector=self.name)

        if self.state is CircuitState.HALF_OPEN:
            if self._half_open_in_flight:
                # Só uma sonda por vez; as demais falham rápido.
                raise CircuitOpenError(self.name, self.cooldown)
            self._half_open_in_flight = True

    def record_success(self) -> None:
        if self.state is not CircuitState.CLOSED:
            logger.info("circuit_closed", connector=self.name)
        self.state = CircuitState.CLOSED
        self.failures = 0
        self._half_open_in_flight = False

    def record_failure(self) -> None:
        self.failures += 1
        self._half_open_in_flight = False
        if self.state is CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            if self.state is not CircuitState.OPEN:
                logger.warning(
                    "circuit_opened",
                    connector=self.name,
                    failures=self.failures,
                    cooldown=self.cooldown,
                )
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()

    def snapshot(self) -> dict:
        return {
            "connector": self.name,
            "state": str(self.state),
            "failures": self.failures,
            "retry_in": (
                max(0.0, self.cooldown - (time.monotonic() - self.opened_at))
                if self.state is CircuitState.OPEN
                else 0.0
            ),
        }


@dataclass(frozen=True)
class ConnectorProfile:
    """Orçamento operacional declarado de uma fonte."""

    name: str
    rate_limit: float = 10.0
    rate_period: float = 1.0
    max_concurrency: int = 5
    timeout: float = 30.0
    max_retries: int = 3
    failure_threshold: int = 5
    cooldown: float = 60.0
    # Fontes cuja ausência degrada a cobertura mas não invalida a busca. Uma
    # fonte não-essencial em timeout vira nota em `limitations`; uma essencial
    # fora do ar muda o status do pack para `partial`.
    essential: bool = False

    def breaker(self) -> CircuitBreaker:
        return CircuitBreaker(
            name=self.name,
            failure_threshold=self.failure_threshold,
            cooldown=self.cooldown,
        )


# Perfis conhecidos. `search_literature` monta o fan-out a partir deste registro.
PROFILES: dict[str, ConnectorProfile] = {
    "openalex": ConnectorProfile(
        name="openalex", rate_limit=10, max_concurrency=8, timeout=15, essential=True
    ),
    "crossref": ConnectorProfile(
        name="crossref", rate_limit=20, max_concurrency=6, timeout=20, essential=True
    ),
    "europepmc": ConnectorProfile(
        name="europepmc", rate_limit=8, max_concurrency=4, timeout=20
    ),
    "scielo": ConnectorProfile(
        name="scielo", rate_limit=4, max_concurrency=3, timeout=25, cooldown=120
    ),
    "semantic_scholar": ConnectorProfile(
        # Pool anônimo é compartilhado e estrangula; 1 rps é o teto seguro sem
        # chave, e o cooldown longo evita insistir num 429 em cascata.
        name="semantic_scholar", rate_limit=1, max_concurrency=1, timeout=20, cooldown=180
    ),
    "arxiv": ConnectorProfile(
        name="arxiv", rate_limit=1, rate_period=3.0, max_concurrency=1, timeout=20
    ),
    "unpaywall": ConnectorProfile(name="unpaywall", rate_limit=10, max_concurrency=5, timeout=15),
    "pubmed": ConnectorProfile(name="pubmed", rate_limit=3, max_concurrency=3, timeout=20),
    "bdtd": ConnectorProfile(name="bdtd", rate_limit=2, max_concurrency=2, timeout=30),
}


def profile_for(name: str) -> ConnectorProfile:
    return PROFILES.get(name, ConnectorProfile(name=name))
