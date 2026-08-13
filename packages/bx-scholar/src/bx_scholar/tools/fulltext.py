"""``retrieve_fulltext`` — trechos localizados, nunca o documento inteiro.

Passa o teste de promoção: decidir se vale abrir o texto de *uma obra
específica* depende da pergunta do usuário e do que a busca mostrou. É decisão
do agente. Já resolver acesso, escolher parser, quebrar em seções e ranquear
trechos são etapas que sempre acontecem — ficam internas.

A tool devolve handle + trechos + ``verification_basis``. Nunca o texto
integral: um artigo típico tem 40–70 mil caracteres, e três deles estouram
qualquer orçamento de prompt. O documento fica no store e é recuperável.
"""

from __future__ import annotations

import json

from bx_scholar_core.ids import normalize_doi, resolve_id
from bx_scholar_core.logging import get_logger
from mcp.server.fastmcp import FastMCP
from sqlalchemy import or_, select

from bx_scholar.store import db
from bx_scholar.store.models import Work
from bx_scholar.workflows import fulltext as ft
from bx_scholar.workflows.spans import find_spans

logger = get_logger(__name__)

DESCRIPTION = """Recupera trechos localizados do texto de UMA obra, para conferir o \
que ela realmente diz. Devolve os trechos e a base de verificação — nunca o texto inteiro.

Passe `work` como DOI, PMCID, PMID ou o work_key vindo de uma busca. Passe `question` \
com a afirmação ou a dúvida específica: os trechos são selecionados pelos termos dela.

O campo `verification_basis` é o mais importante da resposta:
- fulltext      = os trechos vêm do texto completo;
- abstract      = SÓ o resumo estava disponível. NÃO afirme que um método, uma \
amostra ou um resultado foi confirmado com base nisso;
- metadata_only = nem resumo há. A existência da referência pode ser verificada; \
o conteúdo dela, não.

Trechos vazios significam "não encontrei os termos neste documento" — nunca \
"o documento contradiz a afirmação"."""


def register(server: FastMCP, settings, cache) -> None:
    @server.tool(name="retrieve_fulltext", description=DESCRIPTION)
    async def retrieve_fulltext(
        work: str,
        question: str | None = None,
        max_spans: int = 8,
    ) -> str:
        work = (work or "").strip()
        if not work:
            raise ValueError("work é obrigatório (DOI, PMCID, PMID ou work_key)")

        row = await _find_work(work)
        if row is None:
            raise ValueError(
                f"obra {work!r} não está no store. Rode search_literature primeiro, "
                f"ou passe um DOI/PMCID que já tenha aparecido numa busca."
            )

        doc = await ft.resolve(row, settings, cache)

        spans = []
        if question and doc.has_text:
            found = find_spans(doc.sections, question, max_spans=max(1, min(max_spans, 20)))
            if found:
                span_ids = await ft.store_spans(doc.doc_id, found)
                spans = [
                    {"span_id": sid, **span.as_dict()}
                    for sid, span in zip(span_ids, found, strict=True)
                ]

        limitations = list(doc.limitations)
        if question and doc.has_text and not spans:
            limitations.append(
                "Nenhum trecho do documento contém os termos da pergunta. Isso significa "
                "que não localizamos evidência — não que o documento contradiga a afirmação."
            )
        if not question and doc.has_text:
            limitations.append(
                "Sem `question`, nenhum trecho foi selecionado. Chame de novo com a "
                "afirmação a conferir para receber os trechos pertinentes."
            )

        payload = {
            "document_id": doc.doc_id,
            "work_key": doc.work_key,
            "title": row.title,
            "doi": row.doi,
            "access_status": str(doc.access_status),
            "verification_basis": str(doc.basis),
            "license": doc.license,
            "source": doc.source,
            "parser": doc.parser,
            "char_count": doc.char_count,
            "sections_available": sorted({s.get("section", "other") for s in doc.sections}),
            "integrity_status": row.integrity_status,
            "spans": spans,
            "limitations": limitations,
        }

        logger.info(
            "retrieve_fulltext_done",
            work_key=doc.work_key,
            access=str(doc.access_status),
            basis=str(doc.basis),
            spans=len(spans),
        )
        return json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False)


async def _find_work(reference: str) -> Work | None:
    """Resolve a referência para uma obra do store, aceitando várias formas."""
    if reference.startswith(("doi:", "pmid:", "pmcid:", "arxiv:", "openalex:", "s2:", "title:")):
        async with db.session() as s:
            return await s.get(Work, reference)

    resolved = resolve_id(reference)
    async with db.session() as s:
        if resolved.is_known:
            row = await s.get(Work, f"{resolved.id_type}:{resolved.value}")
            if row is not None:
                return row

        doi = normalize_doi(reference)
        clauses = []
        if doi:
            clauses.append(Work.doi == doi)
        if resolved.id_type == "pmcid":
            clauses.append(Work.pmcid == resolved.value)
        elif resolved.id_type == "pmid":
            clauses.append(Work.pmid == resolved.value)
        if not clauses:
            return None
        return (await s.execute(select(Work).where(or_(*clauses)))).scalars().first()
