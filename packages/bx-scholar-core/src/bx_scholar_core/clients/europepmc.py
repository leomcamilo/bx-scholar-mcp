"""Europe PMC — cobertura biomédica e texto integral aberto.

Não existia nenhum conector biomédico no v1: um grep por ``pubmed|pmc|entrez``
no repositório inteiro retornava zero. Para um produto que se apresenta como
ferramenta de pesquisa acadêmica, o buraco é grande — saúde é a área com mais
produção indexada do mundo.

Por que Europe PMC e não E-utilities do NCBI: é **uma** API REST que cobre os
registros do PubMed *e* entrega o full text XML dos artigos abertos do PMC. Com
E-utilities seriam três chamadas encadeadas (esearch → esummary → efetch) mais
um caminho separado para o PMC, com 3 req/s sem chave. Aqui é uma chamada por
busca e uma por texto.

O full text é o que sustenta ``verification_basis="fulltext"``. Sem ele, a
verificação de uma afirmação cai para o abstract — e a diferença entre "o
resumo menciona" e "o método está na seção de resultados" é exatamente onde os
erros de sustentação se escondem.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError
from bx_scholar_core.ids import normalize_doi, normalize_pmcid, normalize_pmid
from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.paper import Author, Paper

logger = get_logger(__name__)

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _parse_result(item: dict[str, Any]) -> Paper:
    """Converte um registro ``resultType=core`` em ``Paper``."""
    authors = [
        Author(name=a.get("fullName") or a.get("lastName") or "")
        for a in ((item.get("authorList") or {}).get("author") or [])
        if a.get("fullName") or a.get("lastName")
    ]

    journal_info = item.get("journalInfo") or {}
    journal = (journal_info.get("journal") or {}).get("title") or ""
    issn = (journal_info.get("journal") or {}).get("issn") or ""

    year = None
    for candidate in (item.get("pubYear"), journal_info.get("yearOfPublication")):
        try:
            year = int(candidate)
            break
        except (TypeError, ValueError):
            continue

    # `isOpenAccess` vem como "Y"/"N"; `inEPMC`/`inPMC` dizem se o texto está
    # disponível na plataforma — que é o que de fato importa para o fulltext.
    is_oa = (item.get("isOpenAccess") or "").upper() == "Y"
    has_fulltext = (item.get("hasTextMinedTerms") or "").upper() == "Y" or (
        item.get("inEPMC") or ""
    ).upper() == "Y"

    return Paper(
        title=item.get("title") or "",
        doi=normalize_doi(item.get("doi") or ""),
        year=year,
        journal=journal,
        issn=issn,
        abstract=item.get("abstractText") or "",
        cited_by_count=int(item.get("citedByCount") or 0),
        authors=authors,
        is_open_access=is_oa,
        pmid=normalize_pmid(item.get("pmid") or ""),
        pmcid=normalize_pmcid(item.get("pmcid") or ""),
        has_fulltext=has_fulltext,
        source_api="europepmc",
    )


class EuropePMCClient(AsyncHTTPClient):
    base_url = BASE
    profile_name = "europepmc"

    def __init__(self, polite_email: str, user_agent: str = "", **kwargs: Any) -> None:
        super().__init__(user_agent or f"BX-Scholar (mailto:{polite_email})", **kwargs)
        self._polite_email = polite_email

    async def search(
        self,
        query: str,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 25,
        open_access_only: bool = False,
    ) -> tuple[list[Paper], int]:
        """Busca. Devolve ``(papers, total_reportado)``."""
        parts = [query]
        if year_from and year_to:
            parts.append(f"PUB_YEAR:[{year_from} TO {year_to}]")
        elif year_from:
            parts.append(f"PUB_YEAR:[{year_from} TO 3000]")
        elif year_to:
            parts.append(f"PUB_YEAR:[0 TO {year_to}]")
        if open_access_only:
            parts.append("OPEN_ACCESS:Y")

        resp = await self.get(
            "/search",
            params={
                "query": " AND ".join(parts),
                "format": "json",
                "resultType": "core",
                "pageSize": min(limit, 100),
                "email": self._polite_email,
            },
            cache_policy=("search_results", 3600),
        )
        data = resp.json()
        results = (data.get("resultList") or {}).get("result") or []
        return [_parse_result(r) for r in results], int(data.get("hitCount") or 0)

    async def fulltext_xml(self, pmcid: str) -> str | None:
        """Texto integral JATS de um artigo aberto do PMC.

        Devolve ``None`` quando o artigo não está aberto — que é um resultado
        legítimo e frequente, não um erro. Quem chama precisa registrar
        ``access_status`` e rebaixar a base de verificação, não fingir que tentou.
        """
        pmcid = normalize_pmcid(pmcid)
        if not pmcid:
            return None
        try:
            resp = await self.get(
                f"/{pmcid}/fullTextXML", cache_policy=("fulltext", 30 * 86400)
            )
        except NonRetryableHTTPError as exc:
            logger.info("europepmc_fulltext_unavailable", pmcid=pmcid, status=exc.status_code)
            return None
        text = resp.text
        return text if text and text.lstrip().startswith("<") else None


# --------------------------------------------------------------------------
# Extração de seções do JATS
# --------------------------------------------------------------------------

#: Rótulos de seção que interessam, normalizados. Não é lista exaustiva — o que
#: não casa entra como "other" com o título original preservado.
_SECTION_ALIASES = {
    "introduction": "introduction",
    "background": "introduction",
    "methods": "methods",
    "materials and methods": "methods",
    "method": "methods",
    "metodologia": "methods",
    "results": "results",
    "resultados": "results",
    "discussion": "discussion",
    "discussão": "discussion",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "conclusão": "conclusion",
    "limitations": "limitations",
}


def _text_of(node: ET.Element) -> str:
    return " ".join(t.strip() for t in node.itertext() if t and t.strip())


def parse_jats_sections(xml_text: str) -> list[dict]:
    """Quebra o JATS em seções ``{"section", "title", "text"}``.

    Seção importa para a verificação: uma afirmação sustentada pela seção de
    resultados vale diferente da mesma frase aparecendo na introdução, onde o
    autor está resumindo o trabalho *dos outros*.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("jats_parse_failed", error=str(exc))
        return []

    sections: list[dict] = []

    for abstract in root.iter("abstract"):
        text = _text_of(abstract)
        if text:
            sections.append({"section": "abstract", "title": "Abstract", "text": text})
            break

    body = next(root.iter("body"), None)
    if body is None:
        return sections

    for sec in body.iter("sec"):
        title_node = sec.find("title")
        title = (_text_of(title_node) if title_node is not None else "").strip()
        # Só parágrafos diretos: sem isto, uma seção-mãe repetiria o texto
        # inteiro de todas as suas subseções.
        paragraphs = [_text_of(p) for p in sec.findall("p")]
        text = " ".join(t for t in paragraphs if t).strip()
        if not text:
            continue
        sections.append(
            {
                "section": _SECTION_ALIASES.get(title.lower().strip(" .:"), "other"),
                "title": title or "(sem título)",
                "text": text,
            }
        )

    return sections
