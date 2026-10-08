"""Europe PMC client — PubMed, PMC full text, preprints (bioRxiv, medRxiv...).

No API key. Search uses Europe PMC query syntax; records come with PMID,
PMCID, MeSH and OA flags. Full text is JATS XML, available for the PMC
open-access subset (``inEPMC == "Y"``).
"""

from __future__ import annotations

import re
from typing import Any

from defusedxml import ElementTree as ET

from bx_scholar_core.clients.base import AsyncHTTPClient, NonRetryableHTTPError
from bx_scholar_core.languages import to_iso639_1
from bx_scholar_core.models.paper import Author, Paper, SourceType

EUROPEPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

_TAG = re.compile(r"<[^>]+>")


def _yes(value: Any) -> bool:
    return value == "Y"


def _mesh(record: dict[str, Any]) -> list[str]:
    """Descriptor names, major topics first. A descriptor is major if it or any of
    its qualifiers is flagged major."""
    headings = (record.get("meshHeadingList") or {}).get("meshHeading") or []

    def is_major(h: dict[str, Any]) -> bool:
        quals = (h.get("meshQualifierList") or {}).get("meshQualifier") or []
        return _yes(h.get("majorTopic_YN")) or any(_yes(q.get("majorTopic_YN")) for q in quals)

    ordered = sorted(headings, key=lambda h: not is_major(h))
    return list(dict.fromkeys(h["descriptorName"] for h in ordered if h.get("descriptorName")))


def _source_type(record: dict[str, Any]) -> SourceType:
    pub_types = {t.lower() for t in (record.get("pubTypeList") or {}).get("pubType") or []}
    if record.get("source") == "PPR" or "preprint" in pub_types:
        return "preprint"
    if pub_types & {"journal article", "research-article", "review", "review-article"}:
        return "peer_reviewed"
    return "unknown"


def _pdf_url(record: dict[str, Any]) -> str:
    for u in (record.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
        if u.get("documentStyle") == "pdf" and u.get("availabilityCode") in ("OA", "F"):
            return u.get("url", "")
    return ""


def parse_record(record: dict[str, Any]) -> Paper:
    """Parse a Europe PMC ``resultType=core`` record into a Paper."""
    journal = (record.get("journalInfo") or {}).get("journal") or {}
    authors = [
        Author(
            name=f"{a['firstName']} {a['lastName']}"
            if a.get("firstName") and a.get("lastName")
            else a.get("fullName") or a.get("collectiveName", "")
        )
        for a in ((record.get("authorList") or {}).get("author") or [])[:10]
    ]
    year = str(record.get("pubYear") or "")
    source, rid = record.get("source", ""), record.get("id", "")
    return Paper(
        title=(record.get("title") or "").rstrip("."),
        doi=record.get("doi", ""),
        year=int(year) if year.isdigit() else None,
        authors=authors,
        abstract=_TAG.sub("", record.get("abstractText") or "").strip(),
        cited_by_count=record.get("citedByCount") or 0,
        source_type=_source_type(record),
        journal=journal.get("title")
        or (record.get("bookOrReportDetails") or {}).get("publisher", ""),
        issn=journal.get("issn") or journal.get("essn") or "",
        pmid=record.get("pmid", ""),
        pmcid=record.get("pmcid", ""),
        mesh=_mesh(record),
        language=to_iso639_1(record.get("language", "")),
        is_open_access=_yes(record.get("isOpenAccess")),
        pdf_url=_pdf_url(record),
        landing_url=f"https://europepmc.org/article/{source}/{rid}" if source and rid else "",
        external_ids={"europepmc": f"{source}:{rid}"} if source and rid else {},
        source_api="europepmc",
    )


def _parse_link(item: dict[str, Any]) -> Paper:
    """Citation/reference entries are thin: no abstract, DOI only sometimes."""
    year = str(item.get("pubYear") or "")
    return Paper(
        title=(item.get("title") or "").rstrip("."),
        doi=item.get("doi", ""),
        year=int(year) if year.isdigit() else None,
        authors=[Author(name=item["authorString"])] if item.get("authorString") else [],
        journal=item.get("journalAbbreviation", ""),
        cited_by_count=item.get("citedByCount") or 0,
        pmid=item.get("id", "") if item.get("source") == "MED" else "",
        external_ids={"europepmc": f"{item.get('source')}:{item.get('id')}"}
        if item.get("id")
        else {},
        source_api="europepmc",
    )


def _year_clause(year_from: int | None, year_to: int | None) -> str:
    if not year_from and not year_to:
        return ""
    return f" AND PUB_YEAR:[{year_from or 1800} TO {year_to or 2999}]"


class EuropePMCClient(AsyncHTTPClient):
    """Client for the Europe PMC REST API.

    Rate limit: EBI asks for moderate use; 10 req/s stays well inside it.
    """

    base_url = EUROPEPMC_BASE
    rate_limit = 10.0
    max_rate_period = 1.0

    async def search(
        self,
        query: str,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 25,
    ) -> tuple[list[Paper], int]:
        """Search Europe PMC. Returns (papers, total_hits)."""
        return await self._search(
            f"({query}){_year_clause(year_from, year_to)}",
            min(limit, 100),
            ("search_results", 3600),
        )

    async def lookup(self, id_type: str, value: str) -> Paper | None:
        """Fetch one record by "doi", "pmid" or "pmcid"."""
        # Only DOI may be quoted: EXT_ID:"123" and PMCID:"PMC1" match nothing.
        if id_type == "doi":
            query = f'DOI:"{value}"'
        elif id_type == "pmid" and value.isdigit():
            query = f"EXT_ID:{value} AND SRC:MED"
        elif id_type == "pmcid" and re.fullmatch(r"PMC\d+", value, re.I):
            query = f"PMCID:{value.upper()}"
        else:
            raise ValueError(f"Unsupported {id_type} for Europe PMC lookup: {value!r}")
        papers, _ = await self._search(query, 1, ("paper_metadata", 7 * 86400))
        return papers[0] if papers else None

    async def _search(
        self, query: str, limit: int, cache_policy: tuple[str, int]
    ) -> tuple[list[Paper], int]:
        resp = await self.get(
            "/search",
            params={"query": query, "format": "json", "resultType": "core", "pageSize": limit},
            cache_policy=cache_policy,
        )
        data = resp.json()
        records = (data.get("resultList") or {}).get("result") or []
        return [parse_record(r) for r in records], data.get("hitCount", 0)

    async def citations(self, source: str, record_id: str, limit: int = 25) -> list[Paper]:
        """Papers citing a record (source "MED" + PMID, "PMC" + PMCID, "PPR" + id)."""
        return await self._links(source, record_id, "citations", "citationList", "citation", limit)

    async def references(self, source: str, record_id: str, limit: int = 25) -> list[Paper]:
        """Papers a record cites."""
        return await self._links(
            source, record_id, "references", "referenceList", "reference", limit
        )

    async def _links(
        self, source: str, rid: str, endpoint: str, list_key: str, item_key: str, limit: int
    ) -> list[Paper]:
        resp = await self.get(
            f"/{source}/{rid}/{endpoint}",
            params={"format": "json", "pageSize": min(limit, 1000)},
            cache_policy=("citations", 86400),
        )
        items = (resp.json().get(list_key) or {}).get(item_key) or []
        return [_parse_link(i) for i in items]

    async def fulltext_xml(self, pmcid: str) -> str | None:
        """JATS XML of an open-access PMC article, or None if Europe PMC has no full text."""
        try:
            resp = await self.get(f"/{pmcid}/fullTextXML", cache_policy=("fulltext", 30 * 86400))
        except NonRetryableHTTPError:
            return None
        return resp.text


def _text(el: Any) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _render_section(sec: Any, depth: int = 0) -> str:
    """Paragraphs of a JATS <sec>, with nested section titles kept inline."""
    parts: list[str] = []
    for child in sec:
        if child.tag == "p":
            parts.append(_text(child))
        elif child.tag == "sec":
            title = child.findtext("title")
            body = _render_section(child, depth + 1)
            parts.append(f"{'#' * (depth + 3)} {title}\n\n{body}" if title else body)
        elif child.tag in ("list", "disp-quote", "boxed-text"):
            parts.append(_text(child))
    return "\n\n".join(p for p in parts if p)


def jats_to_sections(xml: str) -> dict[str, Any]:
    """Turn JATS XML into {title, abstract, sections: [{heading, text}]}.

    Figures, tables and the reference list are left out: an agent reading the
    paper needs the argument, and references are better fetched as data.
    """
    root = ET.fromstring(xml)
    front = root.find("front")
    title_el = front.find(".//article-title") if front is not None else None
    abstract_el = front.find(".//abstract") if front is not None else None
    body = root.find("body")
    sections: list[dict[str, str]] = []
    if body is not None:
        loose = [_text(p) for p in body.findall("p")]
        if loose:
            sections.append({"heading": "", "text": "\n\n".join(loose)})
        for sec in body.findall("sec"):
            sections.append({"heading": sec.findtext("title") or "", "text": _render_section(sec)})
    return {
        "title": _text(title_el) if title_el is not None else "",
        "abstract": _text(abstract_el) if abstract_el is not None else "",
        "sections": sections,
    }
