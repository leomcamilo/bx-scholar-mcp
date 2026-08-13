"""Brazilian/lusophone Open Access papers, via OpenAlex.

2026-08-13 — este cliente estava devolvendo lista vazia, sempre, em silêncio.
Os dois caminhos que ele tinha morreram:

- ``host_venue.publisher:SciELO``: o OpenAlex responde ``"host_venue and
  alternate_host_venues are deprecated in favor of locations"`` — erro, não
  resultado vazio. Cai no fallback.
- ``search.scielo.org``: responde **403** com HTML. O fallback checa
  ``content-type: application/json`` e devolve ``[]``.

Resultado: zero resultados desde a aposentadoria do campo no OpenAlex, sem
nenhum sinal — e nenhum teste cobria este arquivo.

Por que ``language:pt`` e não o filtro de publisher consertado: o SciELO como
publisher no OpenAlex cobre 41 mil obras e devolve **63** resultados para
"mobilidade urbana"; ``language:pt`` devolve **62.176**. O eixo de cobertura
lusófona é o idioma, não a etiqueta de publisher. O SciELO não publica API
aberta de busca por palavra-chave, então não há um "SciELO de verdade" a
chamar aqui.

O nome da classe é mantido por compatibilidade — ``tools/search.py`` e as
allowlists de agente referenciam a fonte como ``scielo``.
"""

from __future__ import annotations

from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError
from bx_scholar_core.clients.openalex import _parse_work
from bx_scholar_core.logging import get_logger
from bx_scholar_core.models.paper import Author, Paper

logger = get_logger(__name__)

SCIELO_SEARCH = "https://search.scielo.org/"


class SciELOClient(AsyncHTTPClient):
    """Busca de produção em português via OpenAlex.

    Rate limit: 5 req/s.

    NÃO assuma acesso aberto: a premissa "todo artigo do SciELO é aberto"
    valia para o filtro de publisher e deixou de valer com o filtro de idioma.
    O estado de acesso vem do registro, obra a obra.
    """

    base_url = ""
    rate_limit = 5.0
    max_rate_period = 1.0

    def __init__(self, polite_email: str, user_agent: str = "", **kwargs) -> None:
        ua = user_agent or f"BX-Scholar/0.1.0 (mailto:{polite_email})"
        super().__init__(user_agent=ua, **kwargs)
        self._polite_email = polite_email

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        max_results: int = 20,
    ) -> list[Paper]:
        """Busca produção lusófona via OpenAlex."""
        oa_filter = "language:pt"
        if year_from:
            oa_filter += f",publication_year:>{year_from - 1}"
        if year_to:
            oa_filter += f",publication_year:<{year_to + 1}"

        try:
            resp = await self.get(
                "https://api.openalex.org/works",
                params={
                    "search": query,
                    "filter": oa_filter,
                    # Relevância explícita: sem isto o resultado depende do
                    # default do endpoint, e a busca acadêmica não pode ficar
                    # à mercê disso.
                    "sort": "relevance_score:desc",
                    "per_page": min(max_results, 50),
                    "mailto": self._polite_email,
                },
                cache_policy=("search_results", 3600),
            )
            data = resp.json()
            papers: list[Paper] = []
            for work in data.get("results", []):
                p = _parse_work(work)
                p.source_api = "openalex_pt"
                # ANTES: `p.is_open_access = True` para TODO resultado. Era
                # verdade sob a premissa "tudo que está no SciELO é aberto" —
                # premissa que morreu junto com o filtro de publisher. Com
                # `language:pt` a maioria dos resultados NÃO é aberta, e marcar
                # tudo como aberto é afirmar ao usuário que ele consegue ler o
                # artigo. O dado real vem do próprio registro.
                oa = work.get("open_access") or {}
                p.is_open_access = bool(oa.get("is_oa"))
                if oa_url := oa.get("oa_url"):
                    p.pdf_url = oa_url
                papers.append(p)
            return papers
        except (NonRetryableHTTPError, Exception):
            # O fallback `search.scielo.org` responde 403 e o parser devolvia
            # lista vazia — falha virava "não há nada". Devolver vazio aqui
            # continua sendo o contrato desta função (tools/search.py usa
            # gather(return_exceptions=True)), mas sem fingir que houve
            # segunda tentativa.
            logger.warning("busca_pt_falhou", query=query[:80])
            return []

    async def _search_direct(self, query: str, max_results: int) -> list[Paper]:
        """MORTO — ``search.scielo.org`` responde 403 com HTML desde (ao menos)
        2026-08. Sem chamador desde este commit; mantido só para registro do que
        foi tentado. Não religar sem antes confirmar que o endpoint voltou a
        aceitar requisição automatizada.
        """
        try:
            resp = await self.get(
                SCIELO_SEARCH,
                params={"q": query, "output": "json", "count": min(max_results, 50), "lang": "en"},
                cache_policy=("search_results", 3600),
            )
            if "application/json" not in resp.headers.get("content-type", ""):
                return []

            data = resp.json()
            papers: list[Paper] = []
            for doc in (data.get("docs") or data.get("results") or [])[:max_results]:
                title = (
                    doc.get("title", [""])[0]
                    if isinstance(doc.get("title"), list)
                    else doc.get("title", "")
                )
                year_raw = (
                    doc.get("year_cluster", [""])[0]
                    if isinstance(doc.get("year_cluster"), list)
                    else doc.get("year_cluster", "")
                )
                papers.append(
                    Paper(
                        title=title,
                        doi=doc.get("doi", ""),
                        year=int(year_raw) if year_raw and str(year_raw).isdigit() else None,
                        authors=[Author(name=n) for n in (doc.get("au") or [])[:10]],
                        journal=(
                            doc.get("journal_title", [""])[0]
                            if isinstance(doc.get("journal_title"), list)
                            else doc.get("journal_title", "")
                        ),
                        source_type="peer_reviewed",
                        source_api="scielo_direct",
                        is_open_access=True,
                    )
                )
            return papers
        except Exception:
            return []
