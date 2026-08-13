"""Veículos: SJR, Qualis e JQL saem da memória para o store.

O v1 carrega 53.404 linhas de SJR, 33.339 de Qualis e 842 de JQL como dicts em
memória a cada boot, a partir de 17 MB em ``packages/bx-scholar-core/data/`` que
estão **untracked no git** e são copiados por um shell script que vive em outro
repositório (``/root/bxat/deploy/scripts/refresh_scholar_rankings.sh``). E o
casamento por nome é varredura linear sobre os dois índices a cada miss
(``rankings/service.py:95-114``).

Aqui os dados ficam em tabela, o lookup é indexado, e o refresh é um comando do
próprio CLI. Numa VPS com 245 Mi de RAM livre, tirar ~90 mil linhas do heap de
um serviço residente não é detalhe.

Regra que atravessa o módulo: indicador de veículo **sempre com contexto**.
Qualis é (área, quadriênio, estrato); SJR é (ano, categoria, quartil). Um
periódico A1 em Educação pode ser irrelevante em Engenharia, e nada disso é
garantia sobre um artigo individual — é sobre onde ele foi publicado.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from bx_scholar_core.ids import normalize_issn, normalize_title
from bx_scholar_core.logging import get_logger
from rapidfuzz import fuzz, process
from sqlalchemy import delete, func, select

from bx_scholar.store import db
from bx_scholar.store.models import Venue

logger = get_logger(__name__)

#: Limiar do casamento por nome quando não há ISSN. O v1 usava 85; aqui é mais
#: alto porque casar "Revista Brasileira de Educação" com "Revista Brasileira de
#: Educação Especial" atribui o estrato errado a um periódico diferente — e o
#: erro sai como fato na resposta.
_NAME_MATCH_THRESHOLD = 90

#: Ordem de força dos estratos/quartis, para derivar um `tier` comparável.
_TIER_ORDER = ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4", "B5", "C"]
_QUARTILE_TO_TIER = {"Q1": "A1", "Q2": "A2", "Q3": "B1", "Q4": "B2"}


@dataclass
class VenueAssessment:
    """Avaliação de um veículo, com a proveniência de cada indicador."""

    name: str
    issn_l: str | None = None
    sjr: list[dict] | None = None
    qualis: list[dict] | None = None
    jql: list[dict] | None = None
    matched_by: str = "none"  # issn | name | none
    match_score: float | None = None

    def best_tier(self, *, area: str | None = None) -> str | None:
        """Melhor estrato conhecido, opcionalmente restrito a uma área Qualis.

        Sem ``area``, devolve o melhor estrato em qualquer área — e isso precisa
        ser lido como "em alguma área", não como nota geral do periódico.
        """
        candidates: list[str] = []
        for entry in self.qualis or []:
            if area and area.lower() not in (entry.get("area") or "").lower():
                continue
            estrato = (entry.get("estrato") or "").strip().upper()
            if estrato in _TIER_ORDER:
                candidates.append(estrato)
        for entry in self.sjr or []:
            tier = _QUARTILE_TO_TIER.get((entry.get("quartil") or "").strip().upper())
            if tier:
                candidates.append(tier)
        if not candidates:
            return None
        return min(candidates, key=_TIER_ORDER.index)

    def as_dict(self) -> dict:
        out: dict = {"venue": self.name, "matched_by": self.matched_by}
        if self.issn_l:
            out["issn_l"] = self.issn_l
        if self.qualis:
            out["qualis"] = self.qualis
        if self.sjr:
            out["sjr"] = self.sjr
        if self.jql:
            out["jql"] = self.jql
        if (tier := self.best_tier()) is not None:
            out["best_tier"] = tier
        if self.match_score is not None:
            out["match_score"] = round(self.match_score, 1)
        return out


async def replace_all(rows: list[dict]) -> int:
    """Substitui a tabela inteira. Refresh é recarga, não merge."""
    if not rows:
        logger.warning("venue_refresh_skipped", reason="conjunto vazio")
        return 0
    now = int(time.time())
    async with db.session() as s:
        await s.execute(delete(Venue))
        for row in rows:
            s.add(
                Venue(
                    issn_l=row.get("issn_l"),
                    issns=row.get("issns") or [],
                    name=row["name"],
                    name_norm=normalize_title(row["name"])[:512],
                    publisher=row.get("publisher"),
                    sjr=row.get("sjr") or [],
                    qualis=row.get("qualis") or [],
                    jql=row.get("jql") or [],
                    updated_at=now,
                )
            )
    logger.info("venues_replaced", count=len(rows))
    return len(rows)


async def count() -> int:
    async with db.session() as s:
        return int((await s.execute(select(func.count()).select_from(Venue))).scalar_one())


async def lookup(issns: list[str], names: list[str]) -> dict[str, VenueAssessment]:
    """Resolve veículos em lote. Chave do retorno: o ISSN ou o nome consultado.

    Duas etapas, barata primeiro: ISSN exato numa query indexada; só o que
    sobrar cai no casamento por nome. É o oposto do v1, que varria os índices
    inteiros a cada miss.
    """
    wanted_issns = {normalize_issn(i) for i in issns}
    wanted_issns.discard("")
    result: dict[str, VenueAssessment] = {}

    async with db.session() as s:
        if wanted_issns:
            rows = (
                await s.execute(select(Venue).where(Venue.issn_l.in_(list(wanted_issns))))
            ).scalars().all()
            for row in rows:
                result[row.issn_l] = _to_assessment(row, "issn")

        unresolved = [n for n in names if n and normalize_title(n) not in result]
        if not unresolved:
            return result

        # CUSTO REAL, sem eufemismo: isto lê os 46.818 name_norm da tabela a
        # cada busca que tenha ao menos um veículo não resolvido por ISSN, e o
        # `extractOne` compara contra todos eles, por nome não resolvido. É
        # melhor que o v1 (que varria DOIS índices em memória a cada miss) mas
        # NÃO é barato, e vira leitura de tabela inteira no Postgres.
        #
        # Só não é gargalo hoje porque a maioria das obras traz ISSN e sai na
        # primeira etapa. Quando incomodar, as saídas são cache do catálogo em
        # processo (invalidado pelo load-rankings) ou índice pg_trgm — nesta
        # ordem, porque a primeira é portátil.
        catalogue = (await s.execute(select(Venue.id, Venue.name_norm))).all()
        index = {norm: vid for vid, norm in catalogue if norm}

        matches: dict[str, int] = {}
        for name in unresolved:
            norm = normalize_title(name)
            if not norm:
                continue
            if norm in index:
                matches[name] = index[norm]
                continue
            best = process.extractOne(norm, index.keys(), scorer=fuzz.ratio,
                                      score_cutoff=_NAME_MATCH_THRESHOLD)
            if best:
                matches[name] = index[best[0]]
                result[name] = VenueAssessment(name=name, matched_by="name", match_score=best[1])

        if matches:
            rows = (
                await s.execute(select(Venue).where(Venue.id.in_(list(set(matches.values())))))
            ).scalars().all()
            by_id = {r.id: r for r in rows}
            for name, vid in matches.items():
                row = by_id.get(vid)
                if row is None:
                    continue
                prior = result.get(name)
                assessment = _to_assessment(row, "name")
                assessment.match_score = prior.match_score if prior else 100.0
                result[name] = assessment

    return result


def _to_assessment(row: Venue, matched_by: str) -> VenueAssessment:
    return VenueAssessment(
        name=row.name,
        issn_l=row.issn_l,
        sjr=list(row.sjr or []),
        qualis=list(row.qualis or []),
        jql=list(row.jql or []),
        matched_by=matched_by,
    )
