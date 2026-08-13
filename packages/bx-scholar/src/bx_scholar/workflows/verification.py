"""Verificação de afirmações — três perguntas diferentes, nunca fundidas.

O ponto central do desenho, e o motivo de a fase existir:

1. **A referência existe?**       determinístico  → ``bibliographic_status``
2. **A obra está íntegra?**       determinístico  → ``integrity_status``
3. **Ela sustenta a afirmação?**  julgamento      → ``support_assessment``

O v1 achatava tudo em um ``confidence: "high"`` fixo por branch. Sair com
"DOI existe: true" e "sustenta: 0.87" no mesmo formato é o jeito elegante de
lavar alucinação: o leitor não tem como saber onde termina o fato e começa o
palpite.

Aqui o servidor responde 1 e 2, localiza os trechos que sustentariam 3, e
**declina de julgar 3**: ``verdict="not_assessed"``, ``assessor.type="caller"``.
Quem decide é o agente que chamou, com os trechos e a base à vista. O campo
existe para que, no dia em que houver um avaliador interno, o registro diga
QUEM julgou — em vez de o veredito aparecer do nada.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from enum import StrEnum

from bx_scholar_core.clients.crossref import CrossRefClient
from bx_scholar_core.clients.europepmc import EuropePMCClient
from bx_scholar_core.clients.openalex import OpenAlexClient
from bx_scholar_core.ids import normalize_doi, work_key
from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.paper import Paper

from bx_scholar import WORKFLOW_VERSION
from bx_scholar.workflows import integrity
from bx_scholar.workflows.fulltext import VerificationBasis
from bx_scholar.workflows.matching import (
    BibliographicStatus,
    CitedReference,
    MatchResult,
    pick_best,
    resolve_by_doi,
)

logger = get_logger(__name__)


class SupportVerdict(StrEnum):
    """Vocabulário do julgamento. O servidor só emite ``NOT_ASSESSED``.

    Os demais existem para que quem julga tenha um vocabulário fechado a usar
    ao registrar a decisão — e para que um avaliador interno, no futuro,
    preencha o mesmo campo em vez de inventar outro.
    """

    NOT_ASSESSED = "not_assessed"
    SUPPORTS = "supports"
    PARTIALLY_SUPPORTS = "partially_supports"
    CONTRADICTS = "contradicts"
    INSUFFICIENT = "insufficient"
    UNRELATED = "unrelated"


@dataclass
class ClaimVerification:
    claim_id: str
    claim: str
    cited_as: dict
    bibliographic: MatchResult
    integrity_status: str = "unknown"
    integrity_detail: dict | None = None
    basis: VerificationBasis = VerificationBasis.METADATA_ONLY
    evidence_spans: list[dict] = field(default_factory=list)
    work_key: str | None = None
    document_id: str | None = None
    limitations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """O formato acordado. Os três estados nunca no mesmo campo."""
        out: dict = {
            "claim_id": self.claim_id,
            "claim": self.claim,
            "cited_as": self.cited_as,
            # --- determinístico ---------------------------------------------
            "bibliographic_status": str(self.bibliographic.status),
            "integrity_status": self.integrity_status,
            # --- julgamento -------------------------------------------------
            "support_assessment": {
                "verdict": str(SupportVerdict.NOT_ASSESSED),
                "verification_basis": str(self.basis),
                "evidence_spans": self.evidence_spans,
                "assessor": {
                    "type": "caller",
                    "workflow_version": WORKFLOW_VERSION,
                },
            },
            "human_review": "not_reviewed",
        }
        out.update(self.bibliographic.as_dict())
        out.pop("status", None)  # já está em bibliographic_status
        if self.integrity_detail:
            out["integrity_detail"] = self.integrity_detail
        if self.work_key:
            out["work_key"] = self.work_key
        if self.document_id:
            out["document_id"] = self.document_id
        if self.limitations:
            out["limitations"] = self.limitations
        return out


def new_claim_id() -> str:
    return f"cl_{secrets.token_hex(4)}"


async def _lookup_by_doi(doi: str, settings, cache) -> Paper | None:
    """Resolve um DOI e ENRIQUECE o registro para que a evidência seja possível.

    A primeira versão disto devolvia a primeira fonte que respondesse — e a
    primeira é o CrossRef, que é a autoridade de registro e por isso a certa
    para *identidade*, mas que não traz abstract, nem PMCID, nem link de PDF.
    Resultado medido num DOI real do Lancet Planetary Health: a verificação
    saía com ``verification_basis=metadata_only`` e zero trechos, enquanto o
    OpenAlex tinha 2.431 caracteres de resumo, o Unpaywall tinha o PDF aberto e
    o Europe PMC tinha ``PMC6873641`` com texto integral.

    Ou seja: a obra tinha texto e a ferramenta dizia que não. É o mesmo erro que
    ``merge.py`` já evita — escolher uma fonte em vez de somar o que cada uma
    tem de melhor.
    """
    paper: Paper | None = None

    cr = CrossRefClient(settings.polite_email, settings.user_agent, cache=cache)
    try:
        paper = await cr.get_work(doi)
    except Exception as exc:  # noqa: BLE001
        logger.info("crossref_doi_lookup_failed", doi=doi, error=type(exc).__name__)
    finally:
        await cr.close()

    oa = OpenAlexClient(settings.polite_email, settings.user_agent, cache=cache)
    try:
        from_oa = await oa.get_work(doi)
    except Exception as exc:  # noqa: BLE001
        logger.info("openalex_doi_lookup_failed", doi=doi, error=type(exc).__name__)
        from_oa = None
    finally:
        await oa.close()

    if paper is None:
        paper = from_oa
    elif from_oa is not None:
        # O CrossRef manda na identidade; o OpenAlex preenche o que falta.
        if not paper.abstract and from_oa.abstract:
            paper.abstract = from_oa.abstract
        if not paper.openalex_id:
            paper.openalex_id = from_oa.openalex_id
        if not paper.pdf_url and from_oa.pdf_url:
            paper.pdf_url = from_oa.pdf_url
        if from_oa.is_open_access:
            paper.is_open_access = True

    if paper is None:
        return None

    # PMCID é a chave do texto integral com seções nomeadas — o melhor caso da
    # verificação. Nem CrossRef nem OpenAlex trazem; só o Europe PMC.
    if not paper.pmcid:
        ep = EuropePMCClient(settings.polite_email, settings.user_agent, cache=cache)
        try:
            found, _ = await ep.search(f'DOI:"{doi}"', limit=1)
            if found:
                paper.pmcid = paper.pmcid or found[0].pmcid
                paper.pmid = paper.pmid or found[0].pmid
                if not paper.abstract and found[0].abstract:
                    paper.abstract = found[0].abstract
        except Exception as exc:  # noqa: BLE001
            logger.info("europepmc_doi_lookup_failed", doi=doi, error=type(exc).__name__)
        finally:
            await ep.close()

    return paper


async def _search_candidates(ref: CitedReference, settings, cache) -> list[Paper]:
    """Candidatos para uma referência sem DOI.

    Busca bibliográfica no CrossRef (que indexa a referência como string) e,
    em paralelo conceitual, o OpenAlex por título — bases com recall diferente
    para referência incompleta.
    """
    query = " ".join(filter(None, [" ".join(ref.authors[:2]), ref.title]))[:300]
    if not query.strip():
        return []

    candidates: list[Paper] = []

    cr = CrossRefClient(settings.polite_email, settings.user_agent, cache=cache)
    try:
        _, paper = await cr.verify_citation(
            author=" ".join(ref.authors[:2]),
            year=ref.year or 0,
            title_fragment=ref.title or query,
        )
        if paper:
            candidates.append(paper)
    except Exception as exc:  # noqa: BLE001
        logger.info("crossref_bib_failed", error=type(exc).__name__)
    finally:
        await cr.close()

    oa = OpenAlexClient(settings.polite_email, settings.user_agent, cache=cache)
    try:
        papers, _ = await oa.search(query, per_page=5, sort="relevance_score:desc")
        candidates.extend(papers)
    except Exception as exc:  # noqa: BLE001
        logger.info("openalex_bib_failed", error=type(exc).__name__)
    finally:
        await oa.close()

    return candidates


async def verify_one(
    claim_text: str,
    ref: CitedReference,
    settings,
    cache,
    *,
    locate_evidence: bool = True,
) -> ClaimVerification:
    """Verifica uma afirmação contra a obra que ela cita."""
    result = ClaimVerification(
        claim_id=new_claim_id(),
        claim=claim_text,
        cited_as=ref.as_dict(),
        bibliographic=MatchResult(status=BibliographicStatus.NOT_FOUND),
    )

    # --- 1. bibliográfico ------------------------------------------------
    doi = normalize_doi(ref.doi)
    if doi:
        fetched = await _lookup_by_doi(doi, settings, cache)
        result.bibliographic = resolve_by_doi(ref, fetched)
        if result.bibliographic.status is BibliographicStatus.NOT_FOUND:
            result.limitations.append(
                f"O DOI {doi} não resolve em CrossRef nem OpenAlex. Um DOI que não "
                f"resolve é forte indício de referência inventada ou transcrita errada."
            )
        elif (score := result.bibliographic.score) and score.title < 60:
            result.limitations.append(
                "O DOI resolve, mas para uma obra de título bem diferente do citado — "
                "provável DOI copiado da referência errada."
            )
    elif not ref.is_searchable:
        result.bibliographic = MatchResult(status=BibliographicStatus.INSUFFICIENT_QUERY)
        result.limitations.append(
            "A referência não tem DOI nem título suficiente para procurar. Isto NÃO "
            "significa que a obra não existe — significa que não foi possível procurar."
        )
    else:
        candidates = await _search_candidates(ref, settings, cache)
        result.bibliographic = pick_best(ref, candidates)
        if result.bibliographic.status is BibliographicStatus.AMBIGUOUS:
            result.limitations.append(
                "Dois candidatos com pontuação equivalente: não é possível afirmar a "
                "qual obra a referência se refere. Confira o segundo colocado."
            )

    verified = result.bibliographic.status is BibliographicStatus.VERIFIED
    best = result.bibliographic.best
    if not verified or best is None:
        result.basis = VerificationBasis.METADATA_ONLY
        return result

    result.work_key = work_key(
        doi=best.doi, pmid=best.pmid, pmcid=best.pmcid,
        arxiv_id=best.arxiv_id, openalex_id=best.openalex_id,
        title=best.title, year=best.year,
    )

    # --- 2. integridade ---------------------------------------------------
    if await integrity.mirror_is_populated():
        verdicts = await integrity.lookup([best.doi])
        verdict = verdicts.get(normalize_doi(best.doi))
        if verdict is not None:
            result.integrity_status = str(verdict.status)
            result.integrity_detail = verdict.as_dict()
            if verdict.blocks_selection:
                result.limitations.append(
                    "OBRA RETRATADA. Não use como sustentação sem sinalizar a retratação "
                    "de forma explícita no texto."
                )
            elif verdict.needs_flag:
                result.limitations.append(
                    f"A obra tem aviso publicado ({verdict.status}). Sinalize ao citar."
                )
        elif normalize_doi(best.doi):
            result.integrity_status = str(integrity.IntegrityStatus.CLEAR)
        else:
            result.limitations.append("Obra sem DOI: retratação não pôde ser verificada.")
    else:
        result.limitations.append(
            "Integridade não verificada: espelho de retratações vazio nesta instância."
        )

    # --- 3. evidência (localizar, NÃO julgar) -----------------------------
    if locate_evidence and claim_text.strip():
        result = await _attach_evidence(result, best, claim_text, settings, cache)
    else:
        result.basis = (
            VerificationBasis.ABSTRACT if best.abstract else VerificationBasis.METADATA_ONLY
        )

    return result


async def _attach_evidence(
    result: ClaimVerification, best: Paper, claim_text: str, settings, cache
) -> ClaimVerification:
    """Resolve o texto e localiza trechos. Nunca conclui nada sobre eles."""
    from sqlalchemy import select

    from bx_scholar.store import db
    from bx_scholar.store.models import Work
    from bx_scholar.workflows import fulltext as ft
    from bx_scholar.workflows.spans import find_spans

    async with db.session() as s:
        row = await s.get(Work, result.work_key)
        if row is None:
            row = Work(
                work_key=result.work_key,
                doi=best.doi or None,
                pmid=best.pmid or None,
                pmcid=best.pmcid or None,
                title=best.title,
                year=best.year,
                venue_name=best.journal or None,
                payload=best.model_dump(mode="json"),
                integrity_status=result.integrity_status,
            )
            s.add(row)
        else:
            # Atualiza com o registro ENRIQUECIDO. Sem isto, uma linha criada
            # numa execução anterior (com payload pobre, sem PMCID nem resumo)
            # congela a obra em `metadata_only` para sempre.
            row.integrity_status = result.integrity_status
            row.pmcid = row.pmcid or best.pmcid or None
            row.pmid = row.pmid or best.pmid or None
            merged = dict(row.payload or {})
            for key, value in best.model_dump(mode="json").items():
                if value and not merged.get(key):
                    merged[key] = value
            row.payload = merged
        await s.flush()

    async with db.session() as s:
        row = (await s.execute(select(Work).where(Work.work_key == result.work_key))).scalars().one()
        doc = await ft.resolve(row, settings, cache)

    result.basis = doc.basis
    result.document_id = doc.doc_id
    result.limitations.extend(doc.limitations)

    if not doc.has_text:
        return result

    spans = find_spans(doc.sections, claim_text, max_spans=5)
    if spans:
        span_ids = await ft.store_spans(doc.doc_id, spans)
        result.evidence_spans = [
            {"span_id": sid, **span.as_dict()} for sid, span in zip(span_ids, spans, strict=True)
        ]
    else:
        result.limitations.append(
            "Nenhum trecho do documento contém os termos da afirmação. Isso é ausência "
            "de evidência localizada — NÃO é evidência de que o documento contradiga."
        )
    return result
