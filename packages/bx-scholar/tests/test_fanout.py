"""Cobertura explícita — o teste que o v1 não podia ter.

No v1 o fan-out era ``asyncio.gather(..., return_exceptions=True)`` sem timeout
e com as exceções descartadas. Uma fonte fora do ar produzia exatamente a mesma
resposta que uma fonte sem resultados. Estes testes fixam o comportamento novo:
o que não foi coberto **aparece**.
"""

from __future__ import annotations

import pytest
from bx_scholar_core.clients.base import RetryableHTTPError
from bx_scholar_core.clients.profile import CircuitOpenError

from bx_scholar.connectors.registry import SearchRequest
from bx_scholar.workflows.fanout import Coverage, fan_out

from conftest import make_paper, stub_connector

REQ = SearchRequest(query="mobilidade urbana", limit=10)


class TestCoverage:
    async def test_success_is_complete(self) -> None:
        conns = [stub_connector("openalex", [make_paper(doi="10.1/a")])]
        res = await fan_out(conns, REQ, timeout=1.0)
        assert res.coverage == {"openalex": "complete"}
        assert not res.degraded
        assert res.limitations() == []

    async def test_more_available_is_partial(self) -> None:
        conns = [stub_connector("openalex", [make_paper(doi="10.1/a")], total=180)]
        res = await fan_out(conns, REQ, timeout=1.0)
        assert res.coverage["openalex"] == "partial"
        assert res.reported_total == 180

    async def test_timeout_is_visible_not_silent(self) -> None:
        # O ponto central: uma base lenta NÃO pode parecer uma base vazia.
        conns = [
            stub_connector("openalex", [make_paper(doi="10.1/a")]),
            stub_connector("scielo", [make_paper(doi="10.1/b")], delay=2.0),
        ]
        res = await fan_out(conns, REQ, timeout=0.2)
        assert res.coverage == {
            "openalex": "complete",
            "scielo": "timeout_partial",
        }
        assert res.degraded
        assert any("scielo" in note and "tempo limite" in note for note in res.limitations())

    async def test_open_circuit_is_unavailable(self) -> None:
        conns = [stub_connector("semantic_scholar", raises=CircuitOpenError("semantic_scholar", 30))]
        res = await fan_out(conns, REQ, timeout=1.0)
        assert res.coverage["semantic_scholar"] == "unavailable"
        assert any("indisponível" in n for n in res.limitations())

    async def test_429_is_rate_limited(self) -> None:
        conns = [stub_connector("crossref", raises=RetryableHTTPError(429))]
        res = await fan_out(conns, REQ, timeout=1.0)
        assert res.coverage["crossref"] == "rate_limited"

    async def test_unexpected_error_is_reported_not_swallowed(self) -> None:
        conns = [stub_connector("scielo", raises=ValueError("boom"))]
        res = await fan_out(conns, REQ, timeout=1.0)
        assert res.coverage["scielo"] == "error"
        assert any("scielo" in n for n in res.limitations())

    async def test_one_bad_source_does_not_kill_the_others(self) -> None:
        conns = [
            stub_connector("openalex", [make_paper(doi="10.1/a")]),
            stub_connector("crossref", raises=RetryableHTTPError(500)),
            stub_connector("scielo", [make_paper(doi="10.1/b")]),
        ]
        res = await fan_out(conns, REQ, timeout=1.0)
        assert len(res.papers) == 2
        assert res.coverage["crossref"] == "error"

    async def test_timeout_is_per_connector_not_global(self) -> None:
        # Três fontes a 0,3 s com teto de 1 s passam. Se o teto fosse global e
        # sequencial, 0,9 s ainda passaria — então usamos um teto que só é
        # satisfeito em paralelo.
        conns = [stub_connector(f"s{i}", [make_paper(doi=f"10.1/{i}")], delay=0.3) for i in range(3)]
        res = await fan_out(conns, REQ, timeout=0.5)
        assert set(res.coverage.values()) == {"complete"}
        assert res.elapsed_ms < 900  # paralelo, não somado


class TestCoverageEnum:
    @pytest.mark.parametrize(
        "cov",
        [Coverage.TIMEOUT_PARTIAL, Coverage.UNAVAILABLE, Coverage.RATE_LIMITED, Coverage.ERROR],
    )
    def test_degraded_states_are_not_success(self, cov: Coverage) -> None:
        assert cov not in (Coverage.COMPLETE, Coverage.PARTIAL)


class TestEssentialSources:
    """`essential` era declarado no perfil e nunca lido — o comentário do módulo
    prometia que uma fonte essencial fora do ar mudava o estado do pack."""

    async def test_essential_source_down_is_flagged_loudly(self) -> None:
        conns = [
            stub_connector("openalex", raises=RetryableHTTPError(500), essential=True),
            stub_connector("brasil", [make_paper(doi="10.1/a")]),
        ]
        res = await fan_out(conns, REQ, timeout=1.0)
        assert res.essential_missing == ["openalex"]
        # A nota vem PRIMEIRO: sem OpenAlex o resultado não é "não há
        # literatura", é "não conseguimos procurar direito".
        assert "essencial" in res.limitations()[0]
        assert "busca incompleta" in res.limitations()[0]

    async def test_non_essential_source_down_is_not_alarming(self) -> None:
        conns = [
            stub_connector("openalex", [make_paper(doi="10.1/a")], essential=True),
            stub_connector("arxiv", raises=RetryableHTTPError(500)),
        ]
        res = await fan_out(conns, REQ, timeout=1.0)
        assert res.essential_missing == []
        assert not any("essencial" in n for n in res.limitations())
