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

    async def test_search_bibliographic_returns_all_candidates(self) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(200, json={"message": {"items": [SAMPLE_ITEM, SAMPLE_ITEM]}})

        client = CrossRefClient(polite_email="test@uni.edu")
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        papers = await client.search_bibliographic("Doe AI Adoption Government", rows=5)
        assert len(papers) == 2
        params = dict(seen[0].url.params)
        assert params["query.bibliographic"] == "Doe AI Adoption Government"
        assert "filter" not in params
        await client.close()

    def test_structured_and_organization_authors(self) -> None:
        p = _parse_item(
            {
                "title": ["T"],
                "subtitle": ["S"],
                "published": {"date-parts": [[2019]]},
                "author": [
                    {
                        "given": "Ines",
                        "family": "Mergel",
                        "ORCID": "http://orcid.org/0000-0003-0285-4758",
                    },
                    {"name": "World Health Organization"},
                ],
            }
        )
        assert p.year == 2019  # only "published" present
        assert p.subtitle == "S"
        ines, who = p.authors
        assert (ines.family, ines.given, ines.structure_source) == ("Mergel", "Ines", "source")
        assert ines.orcid == "0000-0003-0285-4758"
        assert (who.kind, who.literal) == ("organization", "World Health Organization")

    def test_authors_not_cut_at_ten(self) -> None:
        item = {"title": ["T"], "author": [{"given": "A", "family": f"F{i}"} for i in range(25)]}
        assert len(_parse_item(item).authors) == 25

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
