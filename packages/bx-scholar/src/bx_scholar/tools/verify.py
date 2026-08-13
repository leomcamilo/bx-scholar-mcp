"""``verify_claims`` — a quarta e última tool pública.

Substitui três tools do v1 (``verify_citation``, ``batch_verify_references`` e
``check_retraction``), que separadas obrigavam o agente a orquestrar a
verificação na mão — e a inventar como combinar os resultados.

O que ela NÃO faz, de propósito: emitir veredito sobre a afirmação ser
sustentada. Isso é julgamento, e o servidor não tem como fazê-lo de forma
auditável. Ele entrega o determinístico resolvido e os trechos localizados; o
veredito é de quem chamou, e o campo ``assessor`` registra isso.
"""

from __future__ import annotations

import asyncio
import json

from bx_scholar_core.logging import get_logger
from mcp.server.fastmcp import FastMCP

from bx_scholar.store import packs
from bx_scholar.workflows.matching import CitedReference, parse_reference
from bx_scholar.workflows.verification import verify_one

logger = get_logger(__name__)

MAX_CLAIMS = 20

DESCRIPTION = """Verifica afirmações contra as obras que elas citam, e verifica \
listas de referências. Use ANTES de apresentar citações ao usuário.

Passe `claims_json`: lista JSON de objetos. Cada um aceita
  {"text": "a afirmação", "cited": {"doi": "10.x/y"}}
  {"text": "a afirmação", "cited": {"title": "...", "authors": ["Silva"], "year": 2020}}
  {"reference": "a referência inteira em texto"}          (só checa se existe)

A resposta separa TRÊS coisas que NÃO devem ser confundidas:

- bibliographic_status: a obra existe e é essa? (verified / not_found / \
ambiguous / insufficient_query). Determinístico.
- integrity_status: retratada, com expressão de preocupação, corrigida? \
Determinístico.
- support_assessment: se a obra SUSTENTA a afirmação. O servidor NÃO julga isto \
— devolve verdict="not_assessed", os trechos localizados em evidence_spans, e \
verification_basis. **Quem julga é você**, lendo os trechos.

Regras ao usar o resultado:
- bibliographic_status=not_found com DOI informado = forte indício de referência \
inventada. Diga isso ao usuário; não apresente a citação como boa.
- integrity_status=retracted = não use como sustentação sem sinalizar.
- verification_basis=metadata_only ou abstract = NÃO afirme que o método, a \
amostra ou o resultado foram confirmados.
- evidence_spans vazio = não localizamos os termos. Não é o documento \
contradizendo a afirmação."""


def register(server: FastMCP, settings, cache) -> None:
    @server.tool(name="verify_claims", description=DESCRIPTION)
    async def verify_claims(claims_json: str) -> str:
        try:
            raw = json.loads(claims_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"claims_json não é JSON válido: {exc}") from exc

        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            raise ValueError("claims_json deve ser uma lista JSON não vazia de afirmações")

        items = raw[:MAX_CLAIMS]
        truncated = len(raw) - len(items)

        parsed: list[tuple[str, CitedReference]] = []
        for entry in items:
            if not isinstance(entry, dict):
                parsed.append(("", parse_reference(str(entry))))
                continue
            if reference := entry.get("reference"):
                # Só referência: existe? Sem afirmação, não há o que sustentar,
                # então nem tentamos localizar trecho.
                parsed.append(("", parse_reference(str(reference))))
                continue
            cited = entry.get("cited") or {}
            ref = CitedReference(
                doi=str(cited.get("doi") or ""),
                title=str(cited.get("title") or ""),
                authors=[str(a) for a in (cited.get("authors") or [])],
                year=int(cited["year"]) if str(cited.get("year") or "").isdigit() else None,
            )
            parsed.append((str(entry.get("text") or ""), ref))

        pack_id = await packs.create_pack(
            kind="verification",
            mode=None,
            query={"claims": len(parsed), "truncated": truncated},
        )

        # Sequencial de propósito: cada verificação faz várias chamadas externas
        # (resolução de DOI, busca bibliográfica, texto integral) e os
        # conectores já têm teto de concorrência próprio. Paralelizar aqui só
        # empurraria todas para o rate limit ao mesmo tempo.
        results = []
        for claim_text, ref in parsed:
            try:
                results.append(
                    await verify_one(
                        claim_text, ref, settings, cache, locate_evidence=bool(claim_text.strip())
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("verify_claim_failed", error=type(exc).__name__)
                from bx_scholar.workflows.matching import BibliographicStatus, MatchResult
                from bx_scholar.workflows.verification import ClaimVerification, new_claim_id

                results.append(
                    ClaimVerification(
                        claim_id=new_claim_id(),
                        claim=claim_text,
                        cited_as=ref.as_dict(),
                        bibliographic=MatchResult(status=BibliographicStatus.NOT_FOUND),
                        limitations=[f"Falha ao verificar ({type(exc).__name__})."],
                    )
                )
            # Respiro entre itens: uma lista de 20 referências não deve virar
            # rajada contra CrossRef e OpenAlex.
            await asyncio.sleep(0.05)

        payload = [r.as_dict() for r in results]
        await packs.add_claim_items(pack_id, payload)

        counts = {
            "claims": len(payload),
            "verified": sum(1 for r in payload if r["bibliographic_status"] == "verified"),
            "not_found": sum(1 for r in payload if r["bibliographic_status"] == "not_found"),
            "ambiguous": sum(1 for r in payload if r["bibliographic_status"] == "ambiguous"),
            "insufficient_query": sum(
                1 for r in payload if r["bibliographic_status"] == "insufficient_query"
            ),
            "retracted": sum(1 for r in payload if r["integrity_status"] == "retracted"),
            "with_fulltext_evidence": sum(
                1
                for r in payload
                if r["support_assessment"]["verification_basis"] == "fulltext"
                and r["support_assessment"]["evidence_spans"]
            ),
        }

        limitations: list[str] = []
        if truncated:
            limitations.append(
                f"{truncated} afirmação(ões) além do limite de {MAX_CLAIMS} não foram "
                f"verificadas. Chame de novo com o restante."
            )
        if counts["not_found"]:
            limitations.append(
                f"{counts['not_found']} referência(s) não localizada(s) — trate como "
                f"possivelmente inventada(s) até confirmação."
            )
        if counts["retracted"]:
            limitations.append(f"{counts['retracted']} obra(s) RETRATADA(S) entre as citadas.")

        await packs.finalize_pack(
            pack_id, counts=counts, coverage={}, limitations=limitations, status="complete"
        )

        projection = {
            "pack_id": pack_id,
            "counts": counts,
            "claims": payload,
            "limitations": limitations,
            "how_to_read_more": (
                f"read_pack(pack_id='{pack_id}', section='claims') para reler; "
                f"retrieve_fulltext(work='<doi>', question='...') para mais trechos."
            ),
            "reminder": (
                "support_assessment.verdict é 'not_assessed' por desenho: o servidor não "
                "julga sustentação. Leia os evidence_spans e conclua você, respeitando "
                "verification_basis."
            ),
        }

        # Teto: uma lista de 20 verificações com trechos passa fácil de 12k chars.
        text = json.dumps(projection, ensure_ascii=False)
        if len(text) > settings.projection_max_chars:
            for claim in projection["claims"]:
                spans = claim["support_assessment"]["evidence_spans"]
                claim["support_assessment"]["evidence_spans"] = spans[:1]
                for span in claim["support_assessment"]["evidence_spans"]:
                    span["text"] = span["text"][:260]
            projection["limitations"].append(
                f"Trechos reduzidos por limite de tamanho — use read_pack(pack_id="
                f"'{pack_id}', section='claims') para o conjunto completo."
            )
            text = json.dumps(projection, ensure_ascii=False)

        logger.info("verify_claims_done", pack_id=pack_id, **counts)
        return text
