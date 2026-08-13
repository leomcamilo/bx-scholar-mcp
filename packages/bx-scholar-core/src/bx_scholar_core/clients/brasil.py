"""Cobertura brasileira e lusófona — o fosso real do produto.

Sobre o conector "SciELO" do v1, e por que este módulo existe
-------------------------------------------------------------

``clients/scielo.py`` não busca no SciELO. Ele monta o filtro
``host_venue.publisher:SciELO`` no OpenAlex e, se falhar, cai para
``search.scielo.org``. Medido em 2026-08-13, os dois caminhos estão mortos:

- o OpenAlex responde ``"host_venue and alternate_host_venues are deprecated in
  favor of locations"`` — erro, não resultado vazio;
- ``search.scielo.org`` devolve **403** com HTML, e o fallback checa
  ``content-type: application/json`` e retorna ``[]``.

Ou seja: **o conector devolve zero resultados, sempre, em silêncio**, para o
produto cujo diferencial declarado é cobertura brasileira. Nenhum teste cobria
esse caminho.

O que dá para fazer de fato
---------------------------

O SciELO não publica API aberta de busca por palavra-chave (o ``search.`` é
protegido). O ArticleMeta é de *recuperação*: aceita PID, não consulta textual,
e não resolve por DOI (404 medido). Então:

1. **Descoberta** vai pelo OpenAlex, mas pelo eixo que realmente existe. O
   filtro de publisher SciELO cobre 41 mil obras e devolveu **63** resultados
   para "mobilidade urbana"; ``language:pt`` devolveu **62.176**. A etiqueta de
   publisher não é o eixo de cobertura brasileira — o idioma é.
2. **Registro autoritativo** vai pelo ArticleMeta, quando há PID: é ele que
   confirma que a obra está no SciELO e traz licença.

O conector se chama ``brasil`` e não ``scielo`` porque é isso que ele é. Chamar
de SciELO o que é um filtro de idioma no OpenAlex foi exatamente o que produziu
o bug silencioso do v1.
"""

from __future__ import annotations

from typing import Any

from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError
from bx_scholar_core.clients.openalex import _parse_work
from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.paper import Paper

logger = get_logger(__name__)

OPENALEX_WORKS = "https://api.openalex.org/works"
ARTICLEMETA = "https://articlemeta.scielo.org/api/v1"

#: Publisher SciELO no OpenAlex (41.001 obras em 2026-08). Usado como sinal de
#: "está no SciELO", não como eixo de cobertura.
SCIELO_PUBLISHER = "P4310312277"


class BrasilClient(AsyncHTTPClient):
    """Descoberta de produção lusófona/brasileira via OpenAlex."""

    base_url = ""
    profile_name = "brasil"

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
        language: str = "pt",
        scielo_only: bool = False,
    ) -> tuple[list[Paper], int]:
        filters = []
        if scielo_only:
            filters.append(f"locations.source.host_organization_lineage:{SCIELO_PUBLISHER}")
        elif language:
            filters.append(f"language:{language}")
        if year_from:
            filters.append(f"publication_year:>{year_from - 1}")
        if year_to:
            filters.append(f"publication_year:<{year_to + 1}")

        resp = await self.get(
            OPENALEX_WORKS,
            params={
                "search": query,
                "filter": ",".join(filters),
                # Relevância, não citação. O padrão do core é
                # `cited_by_count:desc`, que devolve o artigo mais citado que
                # casa vagamente com os termos.
                "sort": "relevance_score:desc",
                "per_page": min(limit, 50),
                "mailto": self._polite_email,
            },
            cache_policy=("search_results", 3600),
        )
        data = resp.json()
        papers: list[Paper] = []
        for work in data.get("results", []):
            paper = _parse_work(work)
            paper.source_api = "brasil"
            oa_url = (work.get("open_access") or {}).get("oa_url")
            if oa_url:
                paper.pdf_url = oa_url
                paper.is_open_access = True
            # SciELO expõe o PID na própria URL do artigo.
            for location in work.get("locations") or []:
                landing = (location.get("landing_page_url") or "")
                if "scielo" in landing and "pid=" in landing:
                    paper.scielo_pid = landing.split("pid=")[-1].split("&")[0]
                    break
            papers.append(paper)

        return papers, int((data.get("meta") or {}).get("count") or 0)


class ArticleMetaClient(AsyncHTTPClient):
    """Registro autoritativo do SciELO por PID.

    Não faz busca — o ArticleMeta não tem consulta textual, e não resolve por
    DOI (404 medido). Serve para confirmar presença no SciELO e obter licença,
    que é o que decide se podemos baixar o texto integral.
    """

    base_url = ARTICLEMETA
    profile_name = "scielo"

    async def by_pid(self, pid: str, collection: str = "scl") -> dict | None:
        if not pid:
            return None
        try:
            resp = await self.get(
                "/article/",
                params={"code": pid, "collection": collection, "format": "json"},
                cache_policy=("work_metadata", 7 * 86400),
            )
        except NonRetryableHTTPError as exc:
            logger.info("articlemeta_miss", pid=pid, status=exc.status_code)
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def license_of(record: dict | None) -> str | None:
        """Licença declarada no registro SciELO.

        O formato do ArticleMeta é MARC-like: campos ``v<numero>`` com listas de
        ``{"_": valor}``. ``v540`` é o campo de licença/direitos.
        """
        if not record:
            return None
        article = record.get("article") or {}
        for field in ("v540", "v706"):
            values = article.get(field) or []
            for item in values:
                text = (item or {}).get("_") or ""
                if "creativecommons" in text.lower() or text.upper().startswith("CC"):
                    return text.strip()[:120]
        return None
