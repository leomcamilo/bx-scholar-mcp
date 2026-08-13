"""Espelho local do Retraction Watch (CC0, via Crossref).

Por que espelho e não consulta por artigo: o gate roda no caminho quente de
toda busca. Uma busca com 200 resultados viraria 200 requisições ao CrossRef —
inviável em latência e deselegante com a API de quem publica o dado de graça.
São ~60k linhas; carregar uma vez por dia e consultar localmente resolve.

O dataset distingue quatro naturezas, e a distinção importa porque cada uma
pede um comportamento diferente (ver ``workflows/integrity``):

- ``Retraction``            → ``retracted``   (fora da seleção)
- ``Expression of concern`` → ``concern``     (entra com alerta)
- ``Correction``            → ``corrected``   (prefira a versão corrigida)
- ``Reinstatement``         → ``reinstated``  (estado restaurado)

Uma mesma obra pode ter várias linhas ao longo do tempo — preocupação seguida
de retratação, ou retratação seguida de reinstalação. A regra é cronológica: o
aviso mais recente descreve o estado atual.
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass

import httpx
from bx_scholar_core.ids import normalize_doi
from bx_scholar_core.logging import get_logger
from sqlalchemy import delete, select

from bx_scholar.store import db
from bx_scholar.store.models import HarvestState, Integrity

logger = get_logger(__name__)

HARVESTER = "retraction_watch"

# Endpoint documentado da Crossref para o dataset completo. O e-mail identifica
# o consumidor (mesma lógica do polite pool) e é exigido pela rota.
CSV_URL = "https://api.labs.crossref.org/data/retractionwatch"

_NATURE_TO_STATUS = {
    "retraction": "retracted",
    "expression of concern": "concern",
    "correction": "corrected",
    "reinstatement": "reinstated",
}

# Desempate quando duas linhas têm a mesma data (ou nenhuma): o estado mais
# restritivo vence. Errar para o lado cauteloso aqui custa uma sinalização a
# mais; errar para o outro lado é citar artigo retratado como válido.
_SEVERITY = {"retracted": 4, "concern": 3, "corrected": 2, "reinstated": 1}


@dataclass
class HarvestReport:
    fetched_rows: int = 0
    stored: int = 0
    skipped_no_doi: int = 0
    skipped_unknown_nature: int = 0
    elapsed_s: float = 0.0
    by_status: dict[str, int] | None = None


def _parse_date(raw: str) -> str:
    """Data do aviso em ISO, quando dá. Formato do dataset varia."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return time.strftime("%Y-%m-%d", time.strptime(raw, fmt))
        except ValueError:
            continue
    return raw[:10]


def _reasons(raw: str) -> list[str]:
    # O campo vem como "+Falsification/Fabrication of Data;+Investigation..."
    return [r.strip().lstrip("+") for r in (raw or "").split(";") if r.strip().lstrip("+")]


def parse_csv(text: str) -> tuple[dict[str, dict], HarvestReport]:
    """Converte o CSV bruto no estado atual por DOI.

    Separado do download de propósito: é a parte que tem regra de negócio e a
    que os testes exercitam, sem rede.
    """
    report = HarvestReport()
    current: dict[str, dict] = {}

    for row in csv.DictReader(io.StringIO(text)):
        report.fetched_rows += 1

        doi = normalize_doi(row.get("OriginalPaperDOI", ""))
        if not doi:
            report.skipped_no_doi += 1
            continue

        nature = (row.get("RetractionNature") or "").strip().lower()
        status = _NATURE_TO_STATUS.get(nature)
        if status is None:
            report.skipped_unknown_nature += 1
            continue

        date = _parse_date(row.get("RetractionDate", ""))
        candidate = {
            "doi": doi,
            "status": status,
            "nature": row.get("RetractionNature", "").strip() or None,
            "notice_doi": normalize_doi(row.get("RetractionDOI", "")) or None,
            "notice_date": date or None,
            "reasons": _reasons(row.get("Reason", "")),
        }

        existing = current.get(doi)
        if existing is None:
            current[doi] = candidate
            continue

        # Aviso mais recente descreve o estado atual; empate vai para o mais
        # restritivo.
        newer = (date or "") > (existing["notice_date"] or "")
        same_date = (date or "") == (existing["notice_date"] or "")
        more_severe = _SEVERITY[status] > _SEVERITY[existing["status"]]
        if newer or (same_date and more_severe):
            current[doi] = candidate

    report.by_status = {}
    for entry in current.values():
        report.by_status[entry["status"]] = report.by_status.get(entry["status"], 0) + 1
    return current, report


async def fetch_csv(polite_email: str, *, timeout: float = 180.0) -> str:
    """Baixa o dataset completo. É grande (dezenas de MB) — timeout generoso."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(
            CSV_URL,
            params={"name": polite_email},
            headers={"User-Agent": f"BX-Scholar (mailto:{polite_email})"},
        )
        resp.raise_for_status()
        return resp.text


async def store_entries(entries: dict[str, dict]) -> int:
    """Substitui o espelho em bloco.

    Substituição total, não merge incremental: uma obra pode ser *removida* do
    dataset (aviso retirado), e um merge deixaria o estado antigo grudado para
    sempre. Roda numa transação, então nunca existe uma janela com o espelho
    vazio para o gate ler.
    """
    if not entries:
        # Salvaguarda: um download que veio vazio (erro de rede silencioso,
        # HTML de erro em vez de CSV) não pode apagar um espelho válido.
        logger.warning("integrity_mirror_refresh_skipped", reason="conjunto vazio")
        return 0

    now = int(time.time())
    async with db.session() as s:
        await s.execute(delete(Integrity))
        for entry in entries.values():
            s.add(
                Integrity(
                    doi=entry["doi"],
                    status=entry["status"],
                    nature=entry["nature"],
                    notice_doi=entry["notice_doi"],
                    notice_date=entry["notice_date"],
                    reasons=entry["reasons"],
                    source=HARVESTER,
                    updated_at=now,
                )
            )
    return len(entries)


async def record_state(report: HarvestReport, *, ok: bool) -> None:
    now = int(time.time())
    async with db.session() as s:
        state = await s.get(HarvestState, HARVESTER)
        if state is None:
            state = HarvestState(name=HARVESTER)
            s.add(state)
        state.last_run_at = now
        if ok:
            state.last_ok_at = now
            state.rows = report.stored
        state.detail = {
            "fetched_rows": report.fetched_rows,
            "stored": report.stored,
            "skipped_no_doi": report.skipped_no_doi,
            "skipped_unknown_nature": report.skipped_unknown_nature,
            "by_status": report.by_status or {},
            "elapsed_s": round(report.elapsed_s, 1),
            "ok": ok,
        }


async def refresh(polite_email: str) -> HarvestReport:
    """Baixa, parseia e substitui o espelho. Ponto de entrada do timer."""
    t0 = time.monotonic()
    try:
        text = await fetch_csv(polite_email)
        entries, report = parse_csv(text)
        report.stored = await store_entries(entries)
        report.elapsed_s = time.monotonic() - t0
        await record_state(report, ok=report.stored > 0)
        logger.info(
            "integrity_mirror_refreshed",
            rows=report.fetched_rows,
            stored=report.stored,
            by_status=report.by_status,
            elapsed_s=round(report.elapsed_s, 1),
        )
        return report
    except Exception as exc:
        report = HarvestReport(elapsed_s=time.monotonic() - t0)
        await record_state(report, ok=False)
        logger.error("integrity_mirror_refresh_failed", error=str(exc))
        raise
