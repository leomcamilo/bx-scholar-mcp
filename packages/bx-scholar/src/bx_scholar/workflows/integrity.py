"""Gate de integridade — roda ANTES de a obra entrar como evidência.

O v1 tinha ``check_retraction`` como tool avulsa, consultando o ``update-to`` do
CrossRef **depois** de a obra já ter sido devolvida como resultado. Checar no
fim é tarde demais: quando o modelo já viu o artigo na lista, ele já pode tê-lo
citado.

Política (decidida no plano):

| Estado       | Comportamento                                                     |
|--------------|-------------------------------------------------------------------|
| ``retracted``| fora da seleção por padrão; permanece no pack, marcado             |
| ``concern``  | entra com alerta grave                                            |
| ``corrected``| entra, apontando para a versão corrigida                          |
| ``reinstated``| entra normalmente; o estado é registrado                         |
| ``unknown``  | entra, mas explicitado — ausência **nunca** vira ``clear``        |

A ressalva que importa: um artigo retratado não some da pesquisa. Ele pode ser
o próprio objeto de estudo. ``include_retracted=True`` traz de volta para a
seleção, e a marcação continua visível. O que se bloqueia é usá-lo como
sustentação **não sinalizada**.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from bx_scholar_core.ids import normalize_doi
from bx_scholar_core.logging import get_logger
from sqlalchemy import select

from bx_scholar.store import db
from bx_scholar.store.models import Integrity

logger = get_logger(__name__)


class IntegrityStatus(StrEnum):
    CLEAR = "clear"
    RETRACTED = "retracted"
    CONCERN = "concern"
    CORRECTED = "corrected"
    REINSTATED = "reinstated"
    UNKNOWN = "unknown"


#: Estados que tiram a obra da seleção por padrão.
BLOCKING = frozenset({IntegrityStatus.RETRACTED})

#: Estados que entram, mas exigem sinalização visível na resposta.
FLAGGING = frozenset({IntegrityStatus.CONCERN, IntegrityStatus.CORRECTED})


@dataclass
class IntegrityVerdict:
    status: IntegrityStatus
    nature: str | None = None
    notice_doi: str | None = None
    notice_date: str | None = None
    reasons: list[str] | None = None

    @property
    def blocks_selection(self) -> bool:
        return self.status in BLOCKING

    @property
    def needs_flag(self) -> bool:
        return self.status in FLAGGING

    def as_dict(self) -> dict:
        out: dict = {"status": str(self.status)}
        if self.nature:
            out["nature"] = self.nature
        if self.notice_doi:
            out["notice_doi"] = self.notice_doi
        if self.notice_date:
            out["notice_date"] = self.notice_date
        if self.reasons:
            out["reasons"] = self.reasons
        return out


async def lookup(dois: list[str]) -> dict[str, IntegrityVerdict]:
    """Consulta o espelho local em lote.

    Espelho local em vez de chamada por artigo é o que torna o gate viável no
    caminho quente: uma busca com 200 resultados vira UMA query, não 200
    requisições ao CrossRef.

    Obras sem DOI e DOIs ausentes do espelho ficam ``unknown`` — nunca
    ``clear``. Inferir limpeza a partir de ausência é como se transforma "não
    verificamos" em "verificamos e está tudo bem".
    """
    normalized = {normalize_doi(d) for d in dois if d}
    normalized.discard("")
    if not normalized:
        return {}

    async with db.session() as s:
        rows = (
            await s.execute(select(Integrity).where(Integrity.doi.in_(list(normalized))))
        ).scalars().all()

    found = {
        row.doi: IntegrityVerdict(
            status=IntegrityStatus(row.status),
            nature=row.nature,
            notice_doi=row.notice_doi,
            notice_date=row.notice_date,
            reasons=list(row.reasons or []),
        )
        for row in rows
    }

    # O espelho lista apenas obras COM problema. Um DOI que não está lá e cuja
    # base foi carregada com sucesso é `clear`; se o espelho nunca foi populado,
    # tudo é `unknown` — ver `mirror_is_populated`.
    return found


async def mirror_is_populated() -> bool:
    """O espelho tem dados? Sem isso, não há como distinguir limpo de não-checado."""
    async with db.session() as s:
        row = (await s.execute(select(Integrity.doi).limit(1))).first()
    return row is not None


async def apply_gate(works, *, include_retracted: bool = False) -> tuple[set[str], list[str]]:
    """Marca cada obra e devolve (chaves selecionadas, notas para o pack).

    ``works`` é a lista de ``MergedWork``; o campo ``integrity_status`` de cada
    uma é preenchido in-place.
    """
    populated = await mirror_is_populated()
    verdicts = await lookup([w.paper.doi for w in works]) if populated else {}

    selected: set[str] = set()
    notes: list[str] = []
    counts = {"retracted": 0, "concern": 0, "corrected": 0, "unknown": 0}

    for w in works:
        doi = normalize_doi(w.paper.doi)
        if not populated:
            verdict = IntegrityVerdict(IntegrityStatus.UNKNOWN)
        elif not doi:
            # Sem DOI não há como consultar retratação. Isso é uma limitação
            # real da obra, não um atestado de saúde.
            verdict = IntegrityVerdict(IntegrityStatus.UNKNOWN)
        else:
            verdict = verdicts.get(doi, IntegrityVerdict(IntegrityStatus.CLEAR))

        w.integrity_status = str(verdict.status)

        if verdict.status is IntegrityStatus.UNKNOWN:
            counts["unknown"] += 1
        elif verdict.status is IntegrityStatus.RETRACTED:
            counts["retracted"] += 1
        elif verdict.status is IntegrityStatus.CONCERN:
            counts["concern"] += 1
        elif verdict.status is IntegrityStatus.CORRECTED:
            counts["corrected"] += 1

        if verdict.blocks_selection and not include_retracted:
            continue
        selected.add(w.work_key)

    if not populated:
        notes.append(
            "Integridade NÃO verificada: o espelho do Retraction Watch está vazio "
            "nesta instância. Nenhuma obra abaixo teve retratação checada."
        )
    else:
        if counts["retracted"]:
            verb = "incluída(s) por pedido explícito" if include_retracted else "fora da seleção"
            notes.append(f"{counts['retracted']} obra(s) retratada(s) — {verb}, marcadas no pack.")
        if counts["concern"]:
            notes.append(
                f"{counts['concern']} obra(s) com expressão de preocupação publicada — "
                f"usar como evidência exige sinalização."
            )
        if counts["corrected"]:
            notes.append(f"{counts['corrected']} obra(s) com correção publicada — prefira a versão corrigida.")
        if counts["unknown"]:
            notes.append(
                f"{counts['unknown']} obra(s) sem DOI: integridade não pôde ser verificada."
            )

    logger.info("integrity_gate", populated=populated, **counts, selected=len(selected))
    return selected, notes
