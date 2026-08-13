"""Resolução de acesso ao texto integral e persistência do documento.

Ordem de tentativa, do mais estruturado para o menos:

1. **Europe PMC XML** — JATS com seções nomeadas. É o melhor caso: a seção vem
   de graça, e seção é informação para a verificação.
2. **PDF aberto** (Unpaywall / OpenAlex ``oa_url``) — extraído com pymupdf,
   sem seções confiáveis.
3. **Abstract** — quando não há texto aberto. Não é falha: é uma base de
   verificação mais fraca, e precisa aparecer como tal.

O que este módulo **não** faz: baixar o que não está aberto. Se não há licença
que permita, o resultado é ``access_status="closed"`` e a verificação daquela
obra fica limitada ao abstract. Contornar paywall não é recurso do produto.

Reuso deliberado: nada aqui chama o ``/api/v1/bxat/ingest`` do BXat. Isso
inverteria a dependência (hoje BXat → scholar) e criaria ciclo no start; além
disso aquele endpoint tem um ``await`` sobre função síncrona em
``bxat_ingest.py:112``, engolido por ``except Exception``.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum

from bx_scholar_core.clients.brasil import ArticleMetaClient
from bx_scholar_core.clients.europepmc import EuropePMCClient, parse_jats_sections
from bx_scholar_core.clients.unpaywall import UnpaywallClient
from bx_scholar_core.logging import get_logger
from sqlalchemy import select

from bx_scholar.store import db
from bx_scholar.store.models import FulltextDoc, FulltextSpan, Work
from bx_scholar.workflows.spans import Span

logger = get_logger(__name__)

MAX_PDF_BYTES = 40 * 1024 * 1024


class AccessStatus(StrEnum):
    OPEN_FULLTEXT = "open_fulltext"
    ABSTRACT_ONLY = "abstract_only"
    CLOSED = "closed"
    USER_PROVIDED = "user_provided"
    NOT_FOUND = "not_found"


class VerificationBasis(StrEnum):
    """O que sustenta uma afirmação sobre esta obra.

    Campo obrigatório em toda saída de verificação. ``metadata_only`` **nunca**
    pode aparentar que uma afirmação científica foi confirmada.
    """

    FULLTEXT = "fulltext"
    ABSTRACT = "abstract"
    METADATA_ONLY = "metadata_only"
    SECONDARY_SOURCE = "secondary_source"
    USER_PROVIDED = "user_provided"


@dataclass
class ResolvedDocument:
    doc_id: str
    work_key: str
    access_status: AccessStatus
    basis: VerificationBasis
    sections: list[dict] = field(default_factory=list)
    license: str | None = None
    source: str | None = None
    parser: str | None = None
    char_count: int = 0
    limitations: list[str] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return bool(self.sections)


def _doc_id() -> str:
    return f"doc_{secrets.token_hex(8)}"


async def _cached_document(work_key: str) -> FulltextDoc | None:
    async with db.session() as s:
        return (
            await s.execute(
                select(FulltextDoc)
                .where(FulltextDoc.work_key == work_key)
                .order_by(FulltextDoc.retrieved_at.desc())
            )
        ).scalars().first()


async def _load_sections(doc_id: str) -> list[dict]:
    async with db.session() as s:
        rows = (
            await s.execute(
                select(FulltextSpan)
                .where(FulltextSpan.doc_id == doc_id)
                .order_by(FulltextSpan.ordinal)
            )
        ).scalars().all()
    return [
        {"section": r.section or "other", "title": r.section_title or "", "text": r.text}
        for r in rows
    ]


async def _persist(doc: ResolvedDocument, raw_text: str) -> None:
    """Guarda documento + seções. As seções ficam como linhas de span com
    ``ordinal`` negativo? Não — ficam como spans de seção inteira, e os trechos
    recortados são derivados na hora da consulta, não persistidos."""
    now = int(time.time())
    async with db.session() as s:
        s.add(
            FulltextDoc(
                doc_id=doc.doc_id,
                work_key=doc.work_key,
                sha256=hashlib.sha256(raw_text.encode("utf-8", "ignore")).hexdigest(),
                access_status=str(doc.access_status),
                license=doc.license,
                source=doc.source,
                parser=doc.parser,
                char_count=doc.char_count,
                sections=[{"section": x["section"], "title": x.get("title", "")} for x in doc.sections],
                retrieved_at=now,
            )
        )
        for i, section in enumerate(doc.sections):
            s.add(
                FulltextSpan(
                    span_id=f"sec_{secrets.token_hex(6)}",
                    doc_id=doc.doc_id,
                    section=section.get("section"),
                    section_title=section.get("title"),
                    ordinal=i,
                    text=section.get("text") or "",
                )
            )


async def resolve(work: Work, settings, cache) -> ResolvedDocument:
    """Resolve o melhor texto disponível para uma obra."""
    cached = await _cached_document(work.work_key)
    # Só reaproveita documento que DE FATO tem texto. Cachear um fracasso é
    # torná-lo permanente: a obra pode ter ganhado PMCID, virado aberta, ou —
    # o caso real que expôs isto — o registro pode ter sido enriquecido depois,
    # e a tentativa anterior ter falhado só por falta de identificador.
    if cached is not None and cached.access_status in (
        AccessStatus.OPEN_FULLTEXT,
        AccessStatus.ABSTRACT_ONLY,
        AccessStatus.USER_PROVIDED,
    ):
        sections = await _load_sections(cached.doc_id)
        return ResolvedDocument(
            doc_id=cached.doc_id,
            work_key=work.work_key,
            access_status=AccessStatus(cached.access_status),
            basis=(
                VerificationBasis.FULLTEXT
                if cached.access_status == AccessStatus.OPEN_FULLTEXT
                else VerificationBasis.ABSTRACT
            ),
            sections=sections,
            license=cached.license,
            source=cached.source,
            parser=cached.parser,
            char_count=cached.char_count or 0,
        )

    payload = work.payload or {}
    doc = ResolvedDocument(
        doc_id=_doc_id(),
        work_key=work.work_key,
        access_status=AccessStatus.NOT_FOUND,
        basis=VerificationBasis.METADATA_ONLY,
    )

    # 1. Europe PMC — o único caminho que entrega seções nomeadas.
    pmcid = payload.get("pmcid") or ""
    if pmcid:
        client = EuropePMCClient(settings.polite_email, cache=cache)
        try:
            xml = await client.fulltext_xml(pmcid)
            if xml:
                sections = parse_jats_sections(xml)
                if sections:
                    doc.sections = sections
                    doc.access_status = AccessStatus.OPEN_FULLTEXT
                    doc.basis = VerificationBasis.FULLTEXT
                    doc.source = "europepmc"
                    doc.parser = "jats"
                    doc.license = "open (PMC)"
                    doc.char_count = sum(len(s["text"]) for s in sections)
                    await _persist(doc, xml)
                    return doc
        finally:
            await client.close()

    # 2. PDF aberto.
    doi = payload.get("doi") or work.doi or ""
    pdf_url = payload.get("pdf_url") or ""
    oa_license = None
    if doi and not pdf_url:
        client = UnpaywallClient(settings.polite_email, cache=cache)
        try:
            status = await client.check_oa(doi)
            if status:
                pdf_url = status.get("pdf_url") or status.get("oa_url") or ""
                oa_license = status.get("license")
        except Exception as exc:  # noqa: BLE001
            logger.info("unpaywall_failed", doi=doi, error=str(exc))
        finally:
            await client.close()

    if pdf_url:
        text = await _fetch_pdf_text(pdf_url, settings)
        if text:
            doc.sections = [{"section": "other", "title": "Documento completo", "text": text}]
            doc.access_status = AccessStatus.OPEN_FULLTEXT
            doc.basis = VerificationBasis.FULLTEXT
            doc.source = "unpaywall/oa"
            doc.parser = "pymupdf"
            doc.license = oa_license
            doc.char_count = len(text)
            doc.limitations.append(
                "Texto extraído de PDF: não há seções nomeadas, então a localização "
                "de um trecho é por posição de caractere, não por seção."
            )
            await _persist(doc, text)
            return doc

    # 3. Abstract — base mais fraca, declarada como tal.
    abstract = payload.get("abstract") or ""
    if abstract:
        doc.sections = [{"section": "abstract", "title": "Abstract", "text": abstract}]
        doc.access_status = AccessStatus.ABSTRACT_ONLY
        doc.basis = VerificationBasis.ABSTRACT
        doc.source = payload.get("source_api") or "metadata"
        doc.parser = "none"
        doc.char_count = len(abstract)
        doc.limitations.append(
            "Somente o resumo está disponível. Uma afirmação sobre método, amostra "
            "ou resultado NÃO pode ser dada como confirmada a partir do resumo."
        )
        await _persist(doc, abstract)
        return doc

    doc.access_status = AccessStatus.CLOSED
    doc.limitations.append(
        "Nem texto integral nem resumo disponíveis: só há metadados. A existência "
        "da referência pode ser verificada; o conteúdo dela, não."
    )
    return doc


async def _fetch_pdf_text(url: str, settings) -> str | None:
    """Baixa e extrai um PDF aberto. Falha vira ``None``, nunca exceção."""
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True, headers={"User-Agent": settings.user_agent}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content
    except Exception as exc:  # noqa: BLE001
        logger.info("pdf_fetch_failed", url=url[:120], error=type(exc).__name__)
        return None

    if len(content) > MAX_PDF_BYTES:
        logger.info("pdf_too_large", url=url[:120], bytes=len(content))
        return None
    if not content.startswith(b"%PDF"):
        return None

    try:
        import pymupdf

        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            return "\n\n".join(page.get_text() for page in pdf)
    except Exception as exc:  # noqa: BLE001
        logger.info("pdf_parse_failed", url=url[:120], error=type(exc).__name__)
        return None


async def store_spans(doc_id: str, spans: list[Span]) -> list[str]:
    """Persiste os trechos devolvidos e retorna seus ids.

    Os ids importam: um relatório de verificação referencia ``evidence_spans``
    por id, e o trecho precisa continuar recuperável depois — senão a evidência
    citada não é auditável.
    """
    ids: list[str] = []
    async with db.session() as s:
        for i, span in enumerate(spans):
            span_id = f"sp_{secrets.token_hex(6)}"
            ids.append(span_id)
            s.add(
                FulltextSpan(
                    span_id=span_id,
                    doc_id=doc_id,
                    section=span.section,
                    section_title=span.section_title,
                    ordinal=1000 + i,
                    char_start=span.char_start,
                    char_end=span.char_end,
                    text=span.text,
                    score=span.score,
                )
            )
    return ids
