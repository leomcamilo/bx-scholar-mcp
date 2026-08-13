"""Fan-out paralelo com teto de tempo e cobertura explícita.

O que muda em relação ao v1 (``tools/search.py:141``):

    await asyncio.gather(*tasks, return_exceptions=True)

Aquilo tinha três defeitos silenciosos. Não havia timeout — a busca custava o
tempo da fonte mais lenta. As exceções eram coletadas e **descartadas**, então
uma fonte fora do ar sumia sem deixar rastro. E o resultado não dizia quais
fontes responderam de fato, o que produz a pior falha possível numa ferramenta
de pesquisa: **falsa impressão de completude**. Uma busca em que o SciELO deu
timeout parece, para o leitor, uma busca em que o SciELO não tinha nada.

Aqui cada conector tem teto próprio, e o resultado carrega um bloco
``coverage`` com o estado real de cada fonte.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum

from bx_scholar_core.clients.base import NonRetryableHTTPError, RetryableHTTPError
from bx_scholar_core.clients.profile import CircuitOpenError
from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.paper import Paper

from bx_scholar.connectors.registry import Connector, SearchRequest

logger = get_logger(__name__)


class Coverage(StrEnum):
    """Estado de uma fonte nesta busca. Vai inteiro para a resposta."""

    COMPLETE = "complete"
    PARTIAL = "partial"  # respondeu, mas truncado pelo limite pedido
    TIMEOUT_PARTIAL = "timeout_partial"  # estourou o teto; o que veio, veio
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"  # circuito aberto — nem tentamos
    ERROR = "error"
    SKIPPED = "skipped"  # fora do modo pedido, ou sem credencial


@dataclass
class SourceResult:
    name: str
    coverage: Coverage
    papers: list[Paper] = field(default_factory=list)
    reported_total: int = 0
    elapsed_ms: float = 0.0
    detail: str = ""


@dataclass
class FanoutResult:
    results: list[SourceResult]
    elapsed_ms: float

    @property
    def papers(self) -> list[Paper]:
        out: list[Paper] = []
        for r in self.results:
            out.extend(r.papers)
        return out

    @property
    def coverage(self) -> dict[str, str]:
        return {r.name: str(r.coverage) for r in self.results}

    @property
    def reported_total(self) -> int:
        return sum(r.reported_total for r in self.results)

    def limitations(self) -> list[str]:
        """Frases legíveis sobre o que faltou.

        Vão para o pack e sobem na projeção. O modelo precisa poder dizer ao
        usuário "não achei" com honestidade sobre o que foi ou não consultado.
        """
        notes: list[str] = []
        for r in self.results:
            if r.coverage is Coverage.TIMEOUT_PARTIAL:
                notes.append(
                    f"{r.name}: excedeu o tempo limite — a cobertura desta fonte "
                    f"está incompleta nesta busca."
                )
            elif r.coverage is Coverage.UNAVAILABLE:
                notes.append(
                    f"{r.name}: indisponível (falhas recentes consecutivas) — "
                    f"não foi consultada."
                )
            elif r.coverage is Coverage.RATE_LIMITED:
                notes.append(f"{r.name}: limite de requisições atingido — cobertura parcial.")
            elif r.coverage is Coverage.ERROR:
                notes.append(f"{r.name}: erro ao consultar ({r.detail}).")
        return notes

    @property
    def degraded(self) -> bool:
        return any(r.coverage not in (Coverage.COMPLETE, Coverage.PARTIAL) for r in self.results)


async def _run_one(conn: Connector, req: SearchRequest, timeout: float) -> SourceResult:
    t0 = time.monotonic()
    try:
        papers, total = await asyncio.wait_for(conn.search(req), timeout=timeout)
        elapsed = (time.monotonic() - t0) * 1000
        cov = Coverage.PARTIAL if total > len(papers) else Coverage.COMPLETE
        return SourceResult(conn.name, cov, papers, total, elapsed)

    except TimeoutError:
        # Não é falha da fonte, é escolha nossa de não esperar mais. Sem
        # resultado parcial recuperável aqui, mas o estado é registrado.
        return SourceResult(
            conn.name,
            Coverage.TIMEOUT_PARTIAL,
            elapsed_ms=(time.monotonic() - t0) * 1000,
            detail=f"timeout de {timeout:.0f}s",
        )

    except CircuitOpenError as exc:
        return SourceResult(
            conn.name,
            Coverage.UNAVAILABLE,
            elapsed_ms=(time.monotonic() - t0) * 1000,
            detail=str(exc),
        )

    except RetryableHTTPError as exc:
        cov = Coverage.RATE_LIMITED if exc.status_code == 429 else Coverage.ERROR
        return SourceResult(
            conn.name, cov, elapsed_ms=(time.monotonic() - t0) * 1000, detail=str(exc)
        )

    except (NonRetryableHTTPError, Exception) as exc:  # noqa: BLE001
        logger.warning("connector_failed", connector=conn.name, error=str(exc))
        return SourceResult(
            conn.name,
            Coverage.ERROR,
            elapsed_ms=(time.monotonic() - t0) * 1000,
            detail=type(exc).__name__,
        )


async def fan_out(
    connectors: list[Connector], req: SearchRequest, *, timeout: float
) -> FanoutResult:
    """Consulta todas as fontes em paralelo. Nenhuma exceção escapa.

    O teto é por conector, não global: uma fonte lenta não come o orçamento das
    outras, e o tempo total fica próximo do teto em vez da soma.
    """
    t0 = time.monotonic()
    results = await asyncio.gather(*(_run_one(c, req, timeout) for c in connectors))
    elapsed = (time.monotonic() - t0) * 1000

    logger.info(
        "fanout_complete",
        query=req.query[:80],
        elapsed_ms=round(elapsed, 1),
        coverage={r.name: str(r.coverage) for r in results},
        papers={r.name: len(r.papers) for r in results},
    )
    return FanoutResult(list(results), elapsed)
