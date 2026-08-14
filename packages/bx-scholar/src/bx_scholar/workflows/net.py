"""Saída HTTP para URLs de origem externa — com guarda de SSRF e teto real.

Por que este módulo existe
--------------------------

``retrieve_fulltext`` baixa PDFs cujo endereço vem de **dado externo**: o
``oa_url`` do OpenAlex, o ``pdf_url`` do Unpaywall. Quem controla um registro
numa dessas bases controla, indiretamente, para onde este serviço faz requisição.

A primeira versão do ``_fetch_pdf_text`` fazia:

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        content = resp.content

Sem validação de esquema, host ou IP, seguindo redirecionamento automaticamente,
num serviço que roda como root numa máquina cujo loopback tem Postgres em 5432 e
o BXat em 8090. E o ``MAX_PDF_BYTES`` era checado **depois** de ``resp.content``
já ter materializado o corpo inteiro na memória — teto decorativo, numa VPS com
245 Mi livres.

O v1 tinha exatamente estas defesas em ``tools/fulltext.py`` (commit ``c391e15``)
e eu as perdi ao reescrever. As checagens abaixo espelham as de lá de propósito,
em vez de inventar critério novo.

Duas decisões que valem explicitar
----------------------------------

**Falha fechada.** Host que não resolve, resolução com erro, IP fora do padrão:
tudo é recusa. Numa guarda de SSRF, "não consegui verificar" tem de significar
"não vou", senão a verificação é enfeite.

**Revalidação por salto.** Não basta checar a primeira URL: um host público que
responde ``302 -> http://127.0.0.1:5432`` contorna qualquer validação feita só na
entrada. Por isso ``follow_redirects=False`` e cada destino é validado de novo.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bx_scholar_core.logging import get_logger

logger = get_logger(__name__)

MAX_REDIRECTS = 3
_ALLOWED_SCHEMES = ("http", "https")


@dataclass
class FetchResult:
    content: bytes
    final_url: str
    content_type: str

    @property
    def is_pdf(self) -> bool:
        return self.content.startswith(b"%PDF") or "pdf" in self.content_type.lower()


def _host_is_public(host: str) -> bool:
    """True apenas se TODOS os endereços de ``host`` forem públicos.

    Todos, não algum: um nome que resolve para um IP público e um loopback
    (truque conhecido de rebinding) tem de ser recusado.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False  # não resolveu = não vai
    if not infos:
        return False

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local  # inclui 169.254.169.254, metadados de nuvem
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def validate_url(url: str) -> str | None:
    """Devolve o motivo da recusa, ou ``None`` se a URL pode ser buscada."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "URL malformada"

    if parsed.scheme not in _ALLOWED_SCHEMES:
        return f"esquema {parsed.scheme!r} não permitido (só http/https)"
    host = parsed.hostname
    if not host:
        return "URL sem host"
    if not _host_is_public(host):
        return f"host interno, privado ou não resolvível: {host}"
    return None


async def fetch_limited(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 60.0,
    user_agent: str = "BX-Scholar",
    client: httpx.AsyncClient | None = None,
) -> FetchResult | None:
    """Busca ``url`` com guarda de SSRF e teto de bytes aplicado DURANTE o download.

    Devolve ``None`` em qualquer recusa ou falha — o chamador trata ausência de
    texto como um estado legítimo (``access_status``), não como erro.

    ``client`` existe para os testes injetarem transporte falso; em produção
    fica ``None`` e um cliente é criado aqui.
    """
    if (reason := validate_url(url)) is not None:
        logger.info("fetch_refused", url=url[:120], reason=reason)
        return None

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=timeout,
            # Desligado de propósito: cada salto é revalidado abaixo.
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )

    current = url
    try:
        for hop in range(MAX_REDIRECTS + 1):
            try:
                async with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            logger.info("fetch_redirect_without_location", url=current[:120])
                            return None
                        nxt = urljoin(current, location)
                        if (reason := validate_url(nxt)) is not None:
                            # O caso que uma validação só na entrada deixaria passar.
                            logger.warning(
                                "fetch_redirect_refused",
                                origem=current[:120],
                                destino=nxt[:120],
                                reason=reason,
                            )
                            return None
                        current = nxt
                        continue

                    if resp.status_code >= 400:
                        logger.info("fetch_http_error", url=current[:120], status=resp.status_code)
                        return None

                    # Teto ANTES de acumular: aborta no meio do stream em vez de
                    # materializar o corpo inteiro para depois medir.
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        logger.info(
                            "fetch_too_large_declared",
                            url=current[:120], bytes=int(declared), cap=max_bytes,
                        )
                        return None

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            logger.info(
                                "fetch_too_large_streamed",
                                url=current[:120], read=total, cap=max_bytes,
                            )
                            return None
                        chunks.append(chunk)

                    return FetchResult(
                        content=b"".join(chunks),
                        final_url=current,
                        content_type=resp.headers.get("content-type", ""),
                    )
            except (httpx.HTTPError, OSError) as exc:
                logger.info("fetch_failed", url=current[:120], error=type(exc).__name__)
                return None

        logger.info("fetch_too_many_redirects", url=url[:120], hops=MAX_REDIRECTS)
        return None
    finally:
        if owns_client:
            await client.aclose()
