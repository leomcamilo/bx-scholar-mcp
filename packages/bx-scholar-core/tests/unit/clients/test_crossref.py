"""Tests for bx_scholar_core.clients.crossref."""

from __future__ import annotations

import httpx

from bx_scholar_core.clients.crossref import CrossRefClient, _parse_item

SAMPLE_ITEM = {
    "DOI": "10.1234/test",
    "title": ["AI Adoption in Government"],
    "published-print": {"date-parts": [[2023]]},
    "author": [
        {"given": "Jane", "family": "Doe"},
        {"given": "John", "family": "Smith"},
    ],
    "is-referenced-by-count": 15,
    "container-title": ["Public Admin Review"],
    "ISSN": ["0033-3352"],
    "type": "journal-article",
}


class TestParseItem:
    def test_basic_fields(self) -> None:
        p = _parse_item(SAMPLE_ITEM)
        assert p.title == "AI Adoption in Government"
        assert p.doi == "10.1234/test"
        assert p.year == 2023
        assert p.cited_by_count == 15
        assert p.source_type == "peer_reviewed"
        assert p.source_api == "crossref"

    def test_authors(self) -> None:
        p = _parse_item(SAMPLE_ITEM)
        assert len(p.authors) == 2
        assert p.authors[0].name == "Jane Doe"

    def test_missing_title(self) -> None:
        item = {**SAMPLE_ITEM, "title": None}
        p = _parse_item(item)
        assert p.title == ""

    def test_missing_year(self) -> None:
        item = {**SAMPLE_ITEM, "published-print": None, "published-online": None}
        p = _parse_item(item)
        assert p.year is None


class TestCrossRefClient:
    async def test_search(self) -> None:
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "message": {
                        "items": [SAMPLE_ITEM],
                        "total-results": 50,
                    }
                },
            )
        )
        client = CrossRefClient(polite_email="test@uni.edu")
        client._client = httpx.AsyncClient(transport=transport)

        papers, total = await client.search("AI government")
        assert total == 50
        assert len(papers) == 1
        assert papers[0].doi == "10.1234/test"
        await client.close()

    async def test_verify_citation_found(self) -> None:
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={"message": {"items": [SAMPLE_ITEM]}},
            )
        )
        client = CrossRefClient(polite_email="test@uni.edu")
        client._client = httpx.AsyncClient(transport=transport)

        verified, match = await client.verify_citation("Doe", 2023, "AI Adoption Government")
        assert verified is True
        assert match is not None
        assert match.doi == "10.1234/test"
        await client.close()

    async def test_verify_citation_not_found(self) -> None:
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={"message": {"items": []}},
            )
        )
        client = CrossRefClient(polite_email="test@uni.edu")
        client._client = httpx.AsyncClient(transport=transport)

        verified, match = await client.verify_citation("Nobody", 2099, "Nonexistent Paper")
        assert verified is False
        assert match is None
        await client.close()

    async def test_check_retraction_not_retracted(self) -> None:
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={"message": {**SAMPLE_ITEM, "update-to": []}},
            )
        )
        client = CrossRefClient(polite_email="test@uni.edu")
        client._client = httpx.AsyncClient(transport=transport)

        status = await client.check_retraction("10.1234/test")
        assert status.retracted is False
        assert status.doi == "10.1234/test"
        await client.close()

    async def test_check_retraction_retracted(self) -> None:
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "message": {
                        **SAMPLE_ITEM,
                        "update-to": [{"type": "retraction", "DOI": "10.1234/retracted"}],
                    }
                },
            )
        )
        client = CrossRefClient(polite_email="test@uni.edu")
        client._client = httpx.AsyncClient(transport=transport)

        status = await client.check_retraction("10.1234/test")
        assert status.retracted is True
        await client.close()
