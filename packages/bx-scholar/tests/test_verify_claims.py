"""``verify_claims`` — os três estados nunca no mesmo campo.

Esta é a garantia que a fase inteira existe para dar. Se um dia alguém fundir
"o DOI existe" com "o artigo sustenta a afirmação" num único número, estes
testes têm de quebrar.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from bx_scholar.store import db
from bx_scholar.store.models import Integrity
from bx_scholar.tools import pack as pack_tool
from bx_scholar.tools import verify as verify_tool
from bx_scholar.workflows import verification
from conftest import make_paper


@pytest.fixture
def server(settings, monkeypatch):
    """Servidor com as buscas externas substituídas por dublês."""

    catalogue = {
        "10.1590/real": make_paper(
            "Arborizacao urbana e temperatura em cidades brasileiras",
            doi="10.1590/real",
            year=2021,
            abstract="Avaliamos o efeito da arborizacao na temperatura. "
            "Foram usadas 42 estacoes meteorologicas ao longo de dezoito meses.",
        ),
        "10.1590/retratado": make_paper(
            "Estudo com dados fabricados", doi="10.1590/retratado", year=2019,
            abstract="Resultados espetaculares.",
        ),
    }

    async def _fake_doi(doi, _settings, _cache):
        return catalogue.get(doi)

    async def _fake_search(ref, _settings, _cache):
        if "arborizacao" in (ref.title or "").lower():
            return [catalogue["10.1590/real"]]
        return []

    monkeypatch.setattr(verification, "_lookup_by_doi", _fake_doi)
    monkeypatch.setattr(verification, "_search_candidates", _fake_search)

    srv = FastMCP("test")
    verify_tool.register(srv, settings, cache=None)
    pack_tool.register(srv, settings)
    return srv


async def call(server: FastMCP, name: str, **kwargs) -> dict:
    result = await server.call_tool(name, kwargs)
    content = result[0] if isinstance(result, tuple) else result
    text = content[0].text if isinstance(content, list) else content
    return json.loads(text)


async def verify(server, claims: list[dict]) -> dict:
    return await call(server, "verify_claims", claims_json=json.dumps(claims))


class TestEpistemicSeparation:
    """O contrato central: fato computacional e julgamento em campos distintos."""

    async def test_three_states_are_separate_fields(self, server, store) -> None:
        out = await verify(server, [{"text": "A arborizacao reduz a temperatura",
                                     "cited": {"doi": "10.1590/real"}}])
        claim = out["claims"][0]
        assert claim["bibliographic_status"] == "verified"
        assert claim["integrity_status"] in {"clear", "unknown"}
        assert "support_assessment" in claim
        # e NENHUM campo único de "confiança" agregando os três
        assert "confidence" not in claim
        assert "confidence" not in claim["support_assessment"]

    async def test_server_never_judges_support(self, server, store) -> None:
        out = await verify(server, [{"text": "A arborizacao reduz a temperatura",
                                     "cited": {"doi": "10.1590/real"}}])
        sa = out["claims"][0]["support_assessment"]
        assert sa["verdict"] == "not_assessed"
        assert sa["assessor"]["type"] == "caller"
        assert sa["assessor"]["workflow_version"]

    async def test_verification_basis_is_always_present(self, server, store) -> None:
        out = await verify(server, [{"text": "qualquer", "cited": {"doi": "10.1590/real"}}])
        assert out["claims"][0]["support_assessment"]["verification_basis"] in {
            "fulltext", "abstract", "metadata_only", "secondary_source", "user_provided"
        }

    async def test_response_reminds_the_caller_it_must_judge(self, server, store) -> None:
        out = await verify(server, [{"text": "x", "cited": {"doi": "10.1590/real"}}])
        assert "not_assessed" in out["reminder"]


class TestBibliographic:
    async def test_unresolvable_doi_is_flagged_as_probable_fabrication(self, server, store) -> None:
        out = await verify(server, [{"text": "afirmação", "cited": {"doi": "10.9999/naoexiste"}}])
        claim = out["claims"][0]
        assert claim["bibliographic_status"] == "not_found"
        assert any("inventada" in n for n in claim["limitations"])
        assert out["counts"]["not_found"] == 1

    async def test_match_by_title_when_there_is_no_doi(self, server, store) -> None:
        out = await verify(server, [{
            "text": "quantas estacoes foram usadas",
            "cited": {"title": "Arborizacao urbana e temperatura em cidades brasileiras",
                      "year": 2021},
        }])
        assert out["claims"][0]["bibliographic_status"] == "verified"
        assert out["claims"][0]["match_score"]["total"] >= 85

    async def test_score_is_computed_not_constant(self, server, store) -> None:
        # O defeito do v1: `confidence: "high"` fixo por branch.
        out = await verify(server, [{
            "text": "x",
            "cited": {"title": "Arborizacao urbana e temperatura em cidades brasileiras",
                      "year": 2021},
        }])
        score = out["claims"][0]["match_score"]
        assert set(score["components"]) == {"title", "year", "author"}
        assert 0 <= score["total"] <= 100

    async def test_unsearchable_reference_is_not_reported_as_nonexistent(
        self, server, store
    ) -> None:
        # "não procuramos" e "não existe" são estados diferentes.
        out = await verify(server, [{"text": "x", "cited": {"year": 2020}}])
        claim = out["claims"][0]
        assert claim["bibliographic_status"] == "insufficient_query"
        assert any("não foi possível procurar" in n for n in claim["limitations"])

    async def test_bare_reference_string_is_accepted(self, server, store) -> None:
        out = await verify(server, [{
            "reference": "SILVA, J. Arborizacao urbana e temperatura em cidades brasileiras. "
                         "Revista X, 2021."
        }])
        assert out["claims"][0]["bibliographic_status"] == "verified"


class TestIntegrity:
    async def test_retracted_work_is_flagged_hard(self, server, store) -> None:
        async with db.session() as s:
            s.add(Integrity(doi="10.1590/retratado", status="retracted", nature="Retraction"))
        out = await verify(server, [{"text": "resultados espetaculares",
                                     "cited": {"doi": "10.1590/retratado"}}])
        claim = out["claims"][0]
        assert claim["bibliographic_status"] == "verified"  # existe
        assert claim["integrity_status"] == "retracted"     # mas retratada
        assert any("RETRATADA" in n for n in claim["limitations"])
        assert out["counts"]["retracted"] == 1

    async def test_empty_mirror_does_not_claim_clear(self, server, store) -> None:
        out = await verify(server, [{"text": "x", "cited": {"doi": "10.1590/real"}}])
        assert out["claims"][0]["integrity_status"] == "unknown"
        assert any("espelho de retratações vazio" in n
                   for n in out["claims"][0]["limitations"])


class TestEvidence:
    async def test_spans_come_from_the_document(self, server, store) -> None:
        out = await verify(server, [{"text": "quantas estacoes meteorologicas foram usadas",
                                     "cited": {"doi": "10.1590/real"}}])
        sa = out["claims"][0]["support_assessment"]
        assert sa["evidence_spans"]
        assert "estacoes" in sa["evidence_spans"][0]["text"]

    async def test_no_matching_span_is_not_a_contradiction(self, server, store) -> None:
        out = await verify(server, [{"text": "blockchain criptomoeda ethereum",
                                     "cited": {"doi": "10.1590/real"}}])
        claim = out["claims"][0]
        assert claim["support_assessment"]["evidence_spans"] == []
        assert any("NÃO é evidência de que o documento contradiga" in n
                   for n in claim["limitations"])

    async def test_reference_only_check_does_not_fabricate_evidence(self, server, store) -> None:
        # Sem afirmação não há o que sustentar; não deve inventar trecho.
        out = await verify(server, [{"reference": "SILVA, J. Arborizacao urbana e temperatura "
                                                  "em cidades brasileiras. Revista X, 2021."}])
        assert out["claims"][0]["support_assessment"]["evidence_spans"] == []


class TestBatchAndPack:
    async def test_batch_counts(self, server, store) -> None:
        out = await verify(server, [
            {"text": "a", "cited": {"doi": "10.1590/real"}},
            {"text": "b", "cited": {"doi": "10.9999/naoexiste"}},
            {"text": "c", "cited": {"year": 2020}},
        ])
        assert out["counts"] == {
            "claims": 3, "verified": 1, "not_found": 1, "ambiguous": 0,
            "insufficient_query": 1, "retracted": 0,
            "with_fulltext_evidence": out["counts"]["with_fulltext_evidence"],
        }

    async def test_claims_are_readable_from_the_pack(self, server, store) -> None:
        # Fecha o achado da auditoria: read_pack(section='claims') estava vazio.
        out = await verify(server, [{"text": "x", "cited": {"doi": "10.1590/real"}}])
        page = await call(server, "read_pack", pack_id=out["pack_id"], section="claims")
        assert page["total"] == 1
        assert page["items"][0]["bibliographic_status"] == "verified"
        assert page["items"][0]["support_assessment"]["verdict"] == "not_assessed"

    async def test_over_the_cap_is_declared_not_silently_dropped(self, server, store) -> None:
        claims = [{"text": f"c{i}", "cited": {"doi": "10.9999/x"}} for i in range(25)]
        out = await verify(server, claims)
        assert out["counts"]["claims"] == 20
        assert any("além do limite" in n for n in out["limitations"])

    async def test_invalid_json_raises(self, server, store) -> None:
        with pytest.raises(Exception, match="JSON"):
            await call(server, "verify_claims", claims_json="{nao é json}")

    async def test_empty_list_raises(self, server, store) -> None:
        with pytest.raises(Exception):
            await call(server, "verify_claims", claims_json="[]")
