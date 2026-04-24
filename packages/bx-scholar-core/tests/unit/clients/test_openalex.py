"""Tests for bx_scholar_core.clients.openalex."""

from __future__ import annotations

import httpx

from bx_scholar_core.clients.openalex import OpenAlexClient, _parse_work, _reconstruct_abstract
from bx_scholar_core.logging import setup_logging

setup_logging(level="WARNING")


SAMPLE_WORK = {
    "id": "https://openalex.org/W12345",
    "title": "AI Adoption in Public Administration",
    "doi": "https://doi.org/10.1234/test",
    "publication_year": 2024,
    "type": "article",
    "cited_by_count": 42,
    "authorships": [
        {"author": {"display_name": "Jane Doe", "id": "https://openalex.org/A111"}},
        {"author": {"display_name": "John Smith", "id": "https://openalex.org/A222"}},
    ],
    "primary_location": {
        "source": {
            "display_name": "Public Admin Review",
            "issn_l": "0033-3352",
        }
    },
    "abstract_inverted_index": {"A": [0], "study": [1], "on": [2], "AI": [3]},
    "open_access": {"is_oa": True},
    "referenced_works": ["https://openalex.org/W999"],
}


class TestReconstructAbstract:
    def test_basic(self) -> None:
        idx = {"Hello": [0], "world": [1]}
        assert _reconstruct_abstract(idx) == "Hello world"

    def test_out_of_order(self) -> None:
        idx = {"world": [1], "Hello": [0]}
        assert _reconstruct_abstract(idx) == "Hello world"

    def test_empty(self) -> None:
        assert _reconstruct_abstract({}) == ""

    def test_multi_occurrence(self) -> None:
        idx = {"the": [0, 2], "cat": [1], "hat": [3]}
        assert _reconstruct_abstract(idx) == "the cat the hat"


class TestParseWork:
    def test_basic_fields(self) -> None:
        p = _parse_work(SAMPLE_WORK)
        assert p.title == "AI Adoption in Public Administration"
        assert p.doi == "10.1234/test"
        assert p.year == 2024
        assert p.cited_by_count == 42
        assert p.source_type == "peer_reviewed"
        assert p.journal == "Public Admin Review"
        assert p.issn == "0033-3352"
        assert p.openalex_id == "W12345"
        assert p.is_open_access is True
        assert p.source_api == "openalex"

    def test_authors(self) -> None:
        p = _parse_work(SAMPLE_WORK)
        assert len(p.authors) == 2
        assert p.authors[0].name == "Jane Doe"
        assert p.authors[0].openalex_id == "A111"

    def test_abstract(self) -> None:
        p = _parse_work(SAMPLE_WORK)
        assert p.abstract == "A study on AI"

    def test_references(self) -> None:
        p = _parse_work(SAMPLE_WORK)
        assert "W999" in p.references

    def test_preprint_type(self) -> None:
        work = {**SAMPLE_WORK, "type": "preprint"}
        p = _parse_work(work)
        assert p.source_type == "preprint"

    def test_missing_source(self) -> None:
        work = {**SAMPLE_WORK, "primary_location": None}
        p = _parse_work(work)
        assert p.journal == ""
        assert p.issn == ""


class TestOpenAlexClient:
    async def test_search(self) -> None:
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={"results": [SAMPLE_WORK], "meta": {"count": 100}},
            )
        )
        client = OpenAlexClient(polite_email="test@uni.edu")
        client._client = httpx.AsyncClient(transport=transport)

        papers, total = await client.search("AI public admin")
        assert total == 100
        assert len(papers) == 1
        assert papers[0].title == "AI Adoption in Public Administration"
        await client.close()

    async def test_get_work(self) -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=SAMPLE_WORK))
        client = OpenAlexClient(polite_email="test@uni.edu")
        client._client = httpx.AsyncClient(transport=transport)

        paper = await client.get_work("10.1234/test")
        assert paper is not None
        assert paper.doi == "10.1234/test"
        await client.close()

    async def test_get_work_not_found(self) -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(404))
        client = OpenAlexClient(polite_email="test@uni.edu")
        client._client = httpx.AsyncClient(transport=transport)

        paper = await client.get_work("10.9999/nonexistent")
        assert paper is None
        await client.close()
