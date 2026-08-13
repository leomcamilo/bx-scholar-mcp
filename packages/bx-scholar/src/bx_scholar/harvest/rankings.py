"""Carga de SJR / Qualis CAPES / JQL para a tabela ``venue``.

Substitui o caminho atual, que é: baixar planilhas à mão, rodar um shell script
que mora em OUTRO repositório (``/root/bxat/deploy/scripts/refresh_scholar_rankings.sh``),
copiar 17 MB para dentro de ``packages/bx-scholar-core/data/`` — diretório
**untracked** — e reiniciar o serviço para que ele recarregue tudo em memória.

Aqui os arquivos de origem seguem sendo fornecidos por fora (são licenciados e
grandes demais para versionar), mas a carga é um comando do próprio CLI e o
resultado é uma tabela consultável.

Pegadinha preservada do script antigo: o XLSX do Qualis publicado pela CAPES tem
a tag ``<dimension>`` errada, e o ``openpyxl`` em ``read_only=True`` enxerga 1
coluna em vez de 4. A solução é abrir sem ``read_only``, pagando memória.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path

from bx_scholar_core.ids import normalize_issn, normalize_title
from bx_scholar_core.logging import get_logger

from bx_scholar.store import venues

logger = get_logger(__name__)


@dataclass
class RankingsReport:
    sjr_rows: int = 0
    qualis_rows: int = 0
    jql_rows: int = 0
    venues: int = 0
    missing: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


@dataclass
class _Accum:
    """Veículo em construção, agregando as três fontes."""

    name: str
    issns: set[str] = field(default_factory=set)
    publisher: str | None = None
    sjr: list[dict] = field(default_factory=list)
    qualis: list[dict] = field(default_factory=list)
    jql: list[dict] = field(default_factory=list)


class _Registry:
    """Índice de construção por ISSN e por nome normalizado."""

    def __init__(self) -> None:
        self._by_issn: dict[str, _Accum] = {}
        self._by_name: dict[str, _Accum] = {}
        self._all: list[_Accum] = []

    def get(self, *, issns: list[str], name: str) -> _Accum:
        for issn in issns:
            if issn in self._by_issn:
                acc = self._by_issn[issn]
                acc.issns.update(issns)
                for i in issns:
                    self._by_issn[i] = acc
                return acc

        norm = normalize_title(name)
        if norm and norm in self._by_name:
            acc = self._by_name[norm]
            acc.issns.update(issns)
            for i in issns:
                self._by_issn[i] = acc
            return acc

        acc = _Accum(name=name, issns=set(issns))
        self._all.append(acc)
        if norm:
            self._by_name[norm] = acc
        for i in issns:
            self._by_issn[i] = acc
        return acc

    @staticmethod
    def _dedupe(entries: list[dict]) -> list[dict]:
        """Remove entradas idênticas mantendo a ordem.

        A planilha da CAPES repete linhas (mesmo periódico, mesma área, mesmo
        estrato) — sem isto, um veículo aparece com 75 "áreas" das quais várias
        são a mesma repetida, e a contagem engana quem lê.
        """
        seen: set[tuple] = set()
        out: list[dict] = []
        for e in entries:
            key = tuple(sorted((k, str(v)) for k, v in e.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        return out

    def rows(self) -> list[dict]:
        out: list[dict] = []
        for acc in self._all:
            acc.qualis = self._dedupe(acc.qualis)
            acc.sjr = self._dedupe(acc.sjr)
            acc.jql = self._dedupe(acc.jql)
            issns = sorted(acc.issns)
            out.append(
                {
                    # issn_l é o ISSN canônico de lookup: o primeiro em ordem,
                    # de forma determinística (o v1 usava "o primeiro do campo",
                    # que muda conforme a fonte listou).
                    "issn_l": issns[0] if issns else None,
                    "issns": issns,
                    "name": acc.name,
                    "publisher": acc.publisher,
                    "sjr": acc.sjr,
                    "qualis": acc.qualis,
                    "jql": acc.jql,
                }
            )
        return out


def _load_sjr(path: Path, reg: _Registry) -> int:
    """SJR: CSV com ';', ISSNs múltiplos separados por vírgula."""
    n = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            title = (row.get("Title") or "").strip()
            if not title:
                continue
            issns = [i for i in (normalize_issn(x) for x in (row.get("Issn") or "").split(",")) if i]
            acc = reg.get(issns=issns, name=title)
            acc.publisher = acc.publisher or (row.get("Publisher") or "").strip() or None
            # SJR sempre COM ano, categoria e quartil — nunca um número solto.
            acc.sjr.append(
                {
                    "ano": (row.get("Year") or "").strip() or None,
                    "quartil": (row.get("SJR Best Quartile") or "").strip() or None,
                    "sjr": (row.get("SJR") or "").strip() or None,
                    "h_index": (row.get("H index") or "").strip() or None,
                    "categoria": (row.get("Areas") or row.get("Categories") or "").strip() or None,
                    "pais": (row.get("Country") or "").strip() or None,
                }
            )
            n += 1
    return n


def _load_qualis(path: Path, reg: _Registry) -> int:
    """Qualis CAPES: XLSX. Uma linha POR ÁREA — o mesmo periódico tem estratos
    diferentes em áreas diferentes, e achatar isso perde a informação toda."""
    import openpyxl

    # read_only=False de propósito: o XLSX da CAPES declara <dimension> errada e
    # o modo read-only enxerga 1 coluna em vez de 4.
    wb = openpyxl.load_workbook(path, read_only=False, data_only=True)
    ws = wb.active
    if ws is None:
        return 0

    rows = ws.iter_rows(values_only=True)
    headers = [str(h or "").strip().lower() for h in next(rows)]

    def col(*needles: str) -> int | None:
        for i, h in enumerate(headers):
            if any(nd in h for nd in needles):
                return i
        return None

    c_issn = col("issn")
    c_title = col("título", "titulo", "title", "periódico", "periodico")
    c_area = col("área", "area")
    c_estrato = col("estrato", "qualis", "classific")
    if c_issn is None or c_estrato is None:
        logger.warning("qualis_columns_not_found", headers=headers)
        wb.close()
        return 0

    n = 0
    for row in rows:
        issn = normalize_issn(str(row[c_issn]) if c_issn < len(row) else "")
        title = str(row[c_title] or "").strip() if c_title is not None and c_title < len(row) else ""
        if not issn and not title:
            continue
        acc = reg.get(issns=[issn] if issn else [], name=title or issn)
        acc.qualis.append(
            {
                "area": str(row[c_area] or "").strip()
                if c_area is not None and c_area < len(row)
                else None,
                "estrato": str(row[c_estrato] or "").strip().upper() or None,
                # O quadriênio não está na planilha por linha; vem do nome do
                # arquivo/edição e é anotado na carga. Guardar sem ele tornaria
                # o dado incomparável entre avaliações.
                "quadrienio": None,
            }
        )
        n += 1

    wb.close()
    return n


def _load_jql(path: Path, reg: _Registry) -> int:
    """JQL (Harzing): CSV com uma coluna por sistema de avaliação."""
    systems = {
        "ajg_abs2024": "ABS",
        "abdc2025": "ABDC",
        "cnrs2020": "CNRS",
        "hceres2021": "HCERES",
        "ft2016": "FT50",
        "vhb": "VHB",
    }
    n = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            title = (row.get("journal") or "").strip()
            issn = normalize_issn(row.get("issn") or "")
            if not title and not issn:
                continue
            acc = reg.get(issns=[issn] if issn else [], name=title or issn)
            for column, system in systems.items():
                value = (row.get(column) or "").strip()
                if value and value not in {"-", "n/a"}:
                    acc.jql.append({"sistema": system, "nota": value})
            if (subject := (row.get("subject") or "").strip()) and acc.jql:
                acc.jql[-1]["area"] = subject
            n += 1
    return n


async def load(data_dir: Path) -> RankingsReport:
    """Lê os três arquivos de ``data_dir`` e recarrega a tabela ``venue``.

    Arquivo ausente é reportado, não fatal: o produto funciona sem ranking (o
    veículo simplesmente não recebe estrato), mas precisa DIZER que não tem.
    """
    t0 = time.monotonic()
    report = RankingsReport()
    reg = _Registry()

    sources = {
        "sjr_rankings.csv": _load_sjr,
        "qualis_capes.xlsx": _load_qualis,
        "jql_rankings.csv": _load_jql,
    }
    for filename, loader in sources.items():
        path = data_dir / filename
        if not path.exists():
            report.missing.append(filename)
            logger.warning("rankings_file_missing", file=str(path))
            continue
        try:
            n = loader(path, reg)
        except Exception as exc:
            report.missing.append(f"{filename} (erro: {exc})")
            logger.error("rankings_load_failed", file=filename, error=str(exc))
            continue
        if filename.startswith("sjr"):
            report.sjr_rows = n
        elif filename.startswith("qualis"):
            report.qualis_rows = n
        else:
            report.jql_rows = n

    rows = reg.rows()
    report.venues = await venues.replace_all(rows)
    report.elapsed_s = time.monotonic() - t0
    logger.info(
        "rankings_loaded",
        sjr=report.sjr_rows,
        qualis=report.qualis_rows,
        jql=report.jql_rows,
        venues=report.venues,
        missing=report.missing,
    )
    return report
