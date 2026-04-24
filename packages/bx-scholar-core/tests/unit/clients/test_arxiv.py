"""Tests for bx_scholar_core.clients.arxiv."""

from __future__ import annotations

import httpx

from bx_scholar_core.clients.arxiv import ArXivClient

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>Large Language Models for Research</title>
    <summary>A survey on LLMs applied to academic research tasks.</summary>
    <published>2024-01-15T00:00:00Z</published>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Li</name></author>
    <arxiv:primary_category term="cs.CL"/>
  </entry>
</feed>"""


class TestArXivClient:
    async def test_search(self) -> None:
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=SAMPLE_XML))
        client = ArXivClient()
        client._client = httpx.AsyncClient(transport=transport)

        papers = await client.search("LLM research")
        assert len(papers) == 1
        p = papers[0]
        assert p.title == "Large Language Models for Research"
        assert p.source_type == "grey_literature"
        assert p.source_api == "arxiv"
        assert p.year == 2024
        assert p.arxiv_id == "2401.12345v1"
        assert len(p.authors) == 2
        assert p.authors[0].name == "Alice Zhang"
        assert "arxiv.org/pdf" in p.pdf_url
        await client.close()

    async def test_empty_results(self) -> None:
        empty_xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>"""
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=empty_xml))
        client = ArXivClient()
        client._client = httpx.AsyncClient(transport=transport)

        papers = await client.search("nonexistent topic xyz")
        assert papers == []
        await client.close()
