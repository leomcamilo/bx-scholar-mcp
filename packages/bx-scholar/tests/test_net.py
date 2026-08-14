"""Guarda de SSRF e teto de bytes.

A URL baixada por ``retrieve_fulltext`` vem de dado externo (OpenAlex,
Unpaywall). A primeira versão do download não validava nada e seguia
redirecionamento automaticamente, num serviço que roda como root numa máquina
com Postgres em 127.0.0.1:5432 e o BXat em 8090.
"""

from __future__ import annotations

import httpx
import pytest

from bx_scholar.workflows.net import fetch_limited, validate_url


class TestValidateURL:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:5432/",          # o Postgres desta máquina
            "http://localhost:8090/",           # o BXat desta máquina
            "http://169.254.169.254/latest/",   # metadados de nuvem
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://[::1]/",
        ],
    )
    def test_internal_targets_are_refused(self, url: str) -> None:
        assert validate_url(url) is not None

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "gopher://x/", "ftp://x/", "data:text/plain,x"],
    )
    def test_non_http_schemes_are_refused(self, url: str) -> None:
        assert "esquema" in (validate_url(url) or "")

    def test_unresolvable_host_fails_closed(self) -> None:
        # "não consegui verificar" tem de significar "não vou".
        assert validate_url("http://nao-existe-mesmo.invalid/x") is not None

    def test_url_without_host_is_refused(self) -> None:
        assert validate_url("http:///caminho") is not None

    def test_public_host_is_allowed(self) -> None:
        assert validate_url("https://api.crossref.org/works") is None


def transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, timeout=5.0
    )


@pytest.fixture
def dns(monkeypatch):
    """Resolução determinística.

    Sem isto os testes de redirecionamento passavam pelo MOTIVO ERRADO: o
    domínio de mentira não resolve, então a URL era recusada já na entrada e a
    revalidação por salto — que é justamente o que se quer testar — nunca
    chegava a rodar. Teste que passa sem exercitar o caminho é teste vazio.
    """
    from bx_scholar.workflows import net

    def fake(host: str) -> bool:
        return host.endswith("exemplo-publico.org")

    monkeypatch.setattr(net, "_host_is_public", fake)


class TestRedirectRevalidation:
    async def test_public_host_redirecting_to_loopback_is_refused(self, dns) -> None:
        """O caso que uma validação só na entrada deixaria passar."""
        visited: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            visited.append(str(request.url))
            if "127.0.0.1" in str(request.url):
                return httpx.Response(200, content=b"%PDF-segredo-interno")
            return httpx.Response(302, headers={"location": "http://127.0.0.1:5432/"})

        async with transport(handler) as client:
            out = await fetch_limited(
                "https://exemplo-publico.org/artigo.pdf",
                max_bytes=1_000_000, client=client,
            )

        assert out is None
        # o host de entrada FOI aceito (senão o teste não testaria nada)…
        assert any("exemplo-publico.org" in u for u in visited)
        # …e o destino interno nunca foi tocado
        assert not any("127.0.0.1" in u for u in visited)

    async def test_redirect_chain_is_bounded(self, dns) -> None:
        hops = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            hops["n"] += 1
            return httpx.Response(302, headers={"location": "https://exemplo-publico.org/proximo"})

        async with transport(handler) as client:
            out = await fetch_limited(
                "https://exemplo-publico.org/a", max_bytes=1_000_000, client=client
            )

        assert out is None
        assert hops["n"] <= 5  # MAX_REDIRECTS + margem

    async def test_redirect_without_location_is_refused(self, dns) -> None:
        async with transport(lambda r: httpx.Response(302)) as client:
            assert await fetch_limited(
                "https://exemplo-publico.org/a", max_bytes=1000, client=client
            ) is None


class TestByteCap:
    async def test_aborts_mid_stream_instead_of_materialising(self, dns) -> None:
        """O teto tem de valer DURANTE o download, não depois."""
        served = {"bytes": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            async def chunks():
                # 1 MB servido em pedaços; o teto é 25 kB. Se o download só
                # medisse no fim, `served` chegaria a 1.000.000.
                for _ in range(100):
                    served["bytes"] += 10_000
                    yield b"x" * 10_000

            return httpx.Response(200, content=chunks())

        async with transport(handler) as client:
            out = await fetch_limited(
                "https://exemplo-publico.org/gigante.pdf", max_bytes=25_000, client=client
            )

        assert out is None
        # parou perto do teto, não leu o corpo inteiro (1 MB)
        assert served["bytes"] < 200_000

    async def test_declared_content_length_over_cap_is_refused_upfront(self, dns) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"x" * 10, headers={"content-length": "999999999"}
            )

        async with transport(handler) as client:
            assert await fetch_limited(
                "https://exemplo-publico.org/a.pdf", max_bytes=1000, client=client
            ) is None

    async def test_under_the_cap_comes_through(self, dns) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-1.7 conteudo",
                                  headers={"content-type": "application/pdf"})

        async with transport(handler) as client:
            out = await fetch_limited(
                "https://exemplo-publico.org/a.pdf", max_bytes=1_000_000, client=client
            )

        assert out is not None
        assert out.is_pdf
        assert out.content.startswith(b"%PDF")


class TestErrors:
    async def test_http_error_returns_none(self, dns) -> None:
        async with transport(lambda r: httpx.Response(404)) as client:
            assert await fetch_limited(
                "https://exemplo-publico.org/a", max_bytes=1000, client=client
            ) is None

    async def test_network_error_returns_none(self, dns) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sem rede")

        async with transport(handler) as client:
            assert await fetch_limited(
                "https://exemplo-publico.org/a", max_bytes=1000, client=client
            ) is None
