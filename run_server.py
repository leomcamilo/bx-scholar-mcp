#!/usr/bin/env python3
"""
BX-Scholar MCP Server — Academic Research Tools
OpenAlex, CrossRef, ArXiv, Tavily + SJR/Qualis Rankings + Citation Verification + Bibliometrics
"""

import os
import sys
import asyncio
import json
import time
import shutil
from pathlib import Path
from typing import Optional, Literal
from datetime import datetime

import httpx
import pandas as pd
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

# --- Configuration ---
POLITE_EMAIL = os.getenv("POLITE_EMAIL", "researcher@example.com")
RANKINGS_URL_SJR = "https://www.scimagojr.com/journalrank.php?out=xls"
RANKINGS_URL_QUALIS = ""  # Must be provided manually - no public URL
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
DATA_DIR = Path(__file__).parent / "data"
OPENALEX_BASE = "https://api.openalex.org"
CROSSREF_BASE = "https://api.crossref.org"
ARXIV_BASE = "https://export.arxiv.org/api/query"

# Rate limiting
_last_arxiv_call = 0.0

# --- Data Indexes (loaded at startup) ---
_sjr_index: dict = {}      # ISSN -> {title, sjr, quartile, h_index, country, area}
_qualis_index: dict = {}   # ISSN -> {title, qualis, area}
_sjr_by_name: dict = {}    # lowercase title -> ISSN


async def _download_sjr():
    """Download SJR rankings CSV from scimagojr.com"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sjr_path = DATA_DIR / "sjr_rankings.csv"
    print(f"[INFO] Downloading SJR rankings...", file=sys.stderr)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(RANKINGS_URL_SJR, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code == 200:
            sjr_path.write_bytes(resp.content)
            print(f"[INFO] SJR downloaded: {len(resp.content)/(1024*1024):.1f}MB", file=sys.stderr)
            return True
        else:
            print(f"[WARN] SJR download failed (HTTP {resp.status_code}). scimagojr.com may block automated downloads.", file=sys.stderr)
            print(f"[WARN] Download manually from https://www.scimagojr.com/journalrank.php and save as {sjr_path}", file=sys.stderr)
            return False


def _load_sjr():
    global _sjr_index, _sjr_by_name
    sjr_path = DATA_DIR / "sjr_rankings.csv"
    if not sjr_path.exists():
        print(f"[WARN] SJR file not found. Download from https://www.scimagojr.com/journalrank.php", file=sys.stderr)
        print(f"[WARN] Save as: {sjr_path}", file=sys.stderr)
        print(f"[WARN] Or run the update_rankings tool after server starts.", file=sys.stderr)
        return
    try:
        df = pd.read_csv(sjr_path, sep=";", on_bad_lines="warn")
        for _, row in df.iterrows():
            issn = str(row.get("Issn", "")).strip()
            title = str(row.get("Title", "")).strip()
            if not issn:
                continue
            # SJR CSV has ISSNs like "12345678, 87654321"
            for single_issn in issn.split(","):
                single_issn = single_issn.strip()
                if len(single_issn) == 8:
                    single_issn = f"{single_issn[:4]}-{single_issn[4:]}"
                entry = {
                    "title": title,
                    "sjr": row.get("SJR", ""),
                    "sjr_quartile": row.get("SJR Best Quartile", ""),
                    "h_index": row.get("H index", ""),
                    "country": row.get("Country", ""),
                    "area": row.get("Areas", ""),
                    "type": row.get("Type", ""),
                    "publisher": row.get("Publisher", ""),
                }
                _sjr_index[single_issn] = entry
            if title:
                _sjr_by_name[title.lower()] = issn.split(",")[0].strip()
        print(f"[INFO] SJR loaded: {len(_sjr_index)} entries", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] Failed to load SJR: {e}", file=sys.stderr)


def _load_qualis():
    global _qualis_index
    qualis_path = DATA_DIR / "qualis_capes.xlsx"
    if not qualis_path.exists():
        print(f"[WARN] Qualis file not found at {qualis_path}", file=sys.stderr)
        return
    try:
        df = pd.read_excel(qualis_path, engine="openpyxl")
        # Qualis columns vary but typically: ISSN, Titulo, Estrato, Area
        issn_col = None
        for c in df.columns:
            if "issn" in c.lower():
                issn_col = c
                break
        if not issn_col:
            print(f"[WARN] No ISSN column found in Qualis. Columns: {list(df.columns)}", file=sys.stderr)
            return
        title_col = next((c for c in df.columns if "tulo" in c.lower() or "title" in c.lower()), None)
        qualis_col = next((c for c in df.columns if "estrato" in c.lower() or "qualis" in c.lower()), None)
        area_col = next((c for c in df.columns if "rea" in c.lower() and "a" in c.lower()), None)

        for _, row in df.iterrows():
            issn = str(row.get(issn_col, "")).strip()
            if not issn or issn == "nan":
                continue
            entry = {
                "title": str(row.get(title_col, "")) if title_col else "",
                "qualis": str(row.get(qualis_col, "")) if qualis_col else "",
                "area": str(row.get(area_col, "")) if area_col else "",
            }
            _qualis_index[issn] = entry
        print(f"[INFO] Qualis loaded: {len(_qualis_index)} entries", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] Failed to load Qualis: {e}", file=sys.stderr)


def _reconstruct_abstract(inverted_index: dict) -> str:
    if not inverted_index:
        return ""
    positions = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def _format_openalex_work(work: dict) -> dict:
    """Format an OpenAlex work into a clean dict."""
    source = (work.get("primary_location") or {}).get("source") or {}
    return {
        "title": work.get("title", ""),
        "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
        "openalex_id": work.get("id", ""),
        "year": work.get("publication_year"),
        "authors": [
            a.get("author", {}).get("display_name", "")
            for a in (work.get("authorships") or [])[:10]
        ],
        "cited_by_count": work.get("cited_by_count", 0),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index") or {}),
        "journal": source.get("display_name", ""),
        "issn": (source.get("issn_l") or ""),
        "type": work.get("type", ""),
        "is_open_access": (work.get("open_access") or {}).get("is_oa", False),
        "source_type": "peer_reviewed" if work.get("type") == "article" else work.get("type", "unknown"),
    }


# --- Create MCP Server ---
mcp = FastMCP("bx-scholar")


# ============================================================
# MCP PROMPTS — Research Workflow Templates
# ============================================================

@mcp.prompt()
def research_pipeline() -> str:
    """Complete academic research pipeline - from topic to submission. Use this to guide a full research project."""
    return """# Academic Research Pipeline

## Phase 0.5: Journal Calibration
- Use get_journal_info(journal_name) to get target journal metadata
- Use get_journal_papers(issn, query=topic) to find recent papers from the journal
- Build a "Journal DNA" profile: methods distribution, theory preferences, writing style, citation patterns

## Phase 1: Discovery
- Use get_keyword_trends(keywords) to map field trends
- Use get_top_journals_for_field(field) to identify venue options
- Use search_openalex(query) for exploratory search to calibrate originality

## Phase 2: Systematic Search (PRISMA)
Execute in parallel:
1. search_openalex(query, year_from, per_page=50)
2. search_crossref(query, year_from, rows=50)
3. search_scielo(query, year_from) — for Brazilian/LATAM journals
4. search_semantic_scholar(query, year) — for TLDR + influential citations
5. get_journal_papers(target_issn, query) — papers from target journal
Deduplicate by DOI.

## Phase 3: Curation
For each paper:
1. lookup_journal_ranking(issn) — get SJR + Qualis
2. Classify tiers: S/A (Q1-Q2), B (Q2-Q3), C (Q3+), Grey (ArXiv)
3. For top papers: get_influential_citations(doi) — check real impact

## Phase 4: Full-Text Access
For each curated paper:
1. check_open_access(doi) — check if OA available
2. If OA: download_pdf(url, path) + extract_pdf_text(path)
3. If paywalled: list for manual download via institutional access

## Phase 5: Literature Review
- Use build_citation_network(seed_dois) to map field structure
- Use find_co_citation_clusters(dois) to identify thematic clusters
- Use get_citation_context(citing, cited) to understand how papers cite each other

## Phase 6: Citation Verification (MANDATORY)
Before ANY written section:
1. batch_verify_references(refs_json) — verify all citations exist
2. check_retraction(doi) — verify no retracted papers
3. If unverified: REMOVE the citation. Never cite unverified references.

## Phase 7: Submission
- Use get_journal_info for formatting requirements
- Use get_author_works to suggest reviewers
- Verify minimum 3-5 papers from target journal are cited"""


@mcp.prompt()
def journal_calibrator(journal_name: str) -> str:
    """Build a Journal DNA profile for calibrating your paper to a target journal."""
    return f"""# Journal Calibrator: {journal_name}

Execute these steps to build a Journal DNA profile:

1. **Metadata**: get_journal_info("{journal_name}")
2. **Recent papers**: get_journal_papers(ISSN, per_page=30) — analyze the 20 most recent
3. **Rankings**: lookup_journal_ranking(ISSN)

From the papers, analyze and document:

**Methodological Profile**: % quantitative vs qualitative vs mixed, dominant methods, typical sample sizes
**Theoretical Profile**: dominant theories, theory-testing vs theory-building ratio
**Writing Profile**: word count range, reference count (median), % refs from last 5 years, hedging level
**Citation Profile**: journal self-citation %, top cited journals, frequent authors (probable reviewers)

**Calibration Checklist**:
- [ ] My method aligns with journal preferences?
- [ ] My theory depth matches expectations?
- [ ] My word count is in range?
- [ ] My reference count matches median?
- [ ] I cite 3-5+ papers from this journal?
- [ ] I cite probable reviewers (genuinely)?"""


@mcp.prompt()
def citation_verification() -> str:
    """Anti-hallucination protocol for verifying all citations before submission."""
    return """# Citation Verification Protocol

NEVER submit a manuscript without running this protocol.

## Step 1: Compile all references
List every citation in your manuscript: author, year, title fragment.

## Step 2: Batch verify
```
batch_verify_references([
    {"author": "Author1", "year": 2020, "title": "key words from title"},
    {"author": "Author2", "year": 2019, "title": "key words from title"},
    ...
])
```

## Step 3: Handle unverified
For each unverified reference:
1. Try verify_citation(author, year, title) with alternative title fragments
2. Try get_paper_by_doi(doi) if you have the DOI
3. If still unverified: **REMOVE THE CITATION**. Do not guess.

## Step 4: Check retractions
For each verified reference with a DOI:
```
check_retraction(doi)
```
If retracted: REMOVE (unless discussing the retraction itself).

## Step 5: Enrich
For verified references missing metadata:
- get_paper_by_doi(doi) for complete metadata
- lookup_journal_ranking(issn) for journal quality verification"""


@mcp.prompt()
def literature_search(topic: str) -> str:
    """Systematic literature search protocol for a given topic."""
    return f"""# Systematic Literature Search: {topic}

## Step 1: Define search terms
Primary: "{topic}"
Generate 3-5 alternative queries (synonyms, broader/narrower terms).

## Step 2: Execute parallel searches
For each query:
1. search_openalex(query, year_from=2019, per_page=50, sort="cited_by_count:desc")
2. search_crossref(query, year_from=2019, rows=50)
3. search_scielo(query, year_from=2019) — essential for Brazilian/LATAM topics
4. search_semantic_scholar(query, year="2019-") — for TLDR and influential citation counts

## Step 3: Deduplicate
Group results by DOI. For papers without DOI, match by title similarity (>90%) + same year.

## Step 4: Snowball from key papers
For the top 5 most-cited papers:
- get_paper_citations(doi, direction="citing") — who cites this? (forward snowballing)
- get_paper_citations(doi, direction="references") — who does this cite? (backward snowballing)

## Step 5: Quality filter
For each paper:
- lookup_journal_ranking(issn) — classify by SJR/Qualis tier
- Tier S/A (Q1-Q2): always include
- Tier B (Q2-Q3): include if relevant
- Tier C+: only if essential (seminal works)
- ArXiv: supplementary only, NEVER primary source for journal publications"""


# ============================================================
# GROUP 1: Literature Search (4 tools)
# ============================================================

@mcp.tool()
async def search_openalex(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    journal_issn: Optional[str] = None,
    type_filter: Optional[str] = None,
    sort: str = "cited_by_count:desc",
    per_page: int = 25,
) -> str:
    """Search OpenAlex for academic papers. Returns structured results with metadata.
    Use journal_issn to search within a specific journal. sort options: cited_by_count:desc, publication_date:desc, relevance_score:desc"""
    params = {
        "search": query,
        "sort": sort,
        "per_page": min(per_page, 50),
        "mailto": POLITE_EMAIL,
    }
    filters = []
    if year_from:
        filters.append(f"publication_year:>{year_from - 1}")
    if year_to:
        filters.append(f"publication_year:<{year_to + 1}")
    if journal_issn:
        filters.append(f"primary_location.source.issn:{journal_issn}")
    if type_filter:
        filters.append(f"type:{type_filter}")
    if filters:
        params["filter"] = ",".join(filters)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(f"{OPENALEX_BASE}/works", params=params)
        resp.raise_for_status()
        data = resp.json()

    results = [_format_openalex_work(w) for w in data.get("results", [])]
    total = data.get("meta", {}).get("count", 0)
    return json.dumps({"total_results": total, "returned": len(results), "results": results}, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_crossref(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    journal_name: Optional[str] = None,
    sort: str = "is-referenced-by-count",
    rows: int = 25,
) -> str:
    """Search CrossRef for academic papers. Good for DOI verification and reference metadata."""
    params = {
        "query": query,
        "sort": sort,
        "order": "desc",
        "rows": min(rows, 50),
    }
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}")
    if year_to:
        filters.append(f"until-pub-date:{year_to}")
    if journal_name:
        params["query.container-title"] = journal_name
    if filters:
        params["filter"] = ",".join(filters)

    headers = {"User-Agent": f"BXScholar/1.0 (mailto:{POLITE_EMAIL})"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(f"{CROSSREF_BASE}/works", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    items = data.get("message", {}).get("items", [])
    results = []
    for item in items:
        results.append({
            "title": (item.get("title") or [""])[0],
            "doi": item.get("DOI", ""),
            "year": (item.get("published-print") or item.get("published-online") or {}).get("date-parts", [[None]])[0][0],
            "authors": [f"{a.get('given', '')} {a.get('family', '')}" for a in (item.get("author") or [])[:10]],
            "cited_by_count": item.get("is-referenced-by-count", 0),
            "journal": (item.get("container-title") or [""])[0],
            "issn": (item.get("ISSN") or [""])[0],
            "type": item.get("type", ""),
            "source_type": "peer_reviewed" if item.get("type") == "journal-article" else item.get("type", "unknown"),
        })
    total = data.get("message", {}).get("total-results", 0)
    return json.dumps({"total_results": total, "returned": len(results), "results": results}, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_arxiv(
    query: str,
    max_results: int = 20,
    sort_by: str = "relevance",
) -> str:
    """Search ArXiv for preprints. WARNING: All ArXiv results are GREY LITERATURE (not peer-reviewed).
    Use only as supplementary source. sort_by: relevance, lastUpdatedDate, submittedDate"""
    global _last_arxiv_call
    # ArXiv rate limit: 3s between requests
    now = time.time()
    wait = 3.0 - (now - _last_arxiv_call)
    if wait > 0:
        await asyncio.sleep(wait)

    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": min(max_results, 50),
        "sortBy": sort_by,
        "sortOrder": "descending",
    }

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(ARXIV_BASE, params=params)
        resp.raise_for_status()
    _last_arxiv_call = time.time()

    # Parse XML
    import xml.etree.ElementTree as ET
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(resp.text)
    results = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.find("atom:title", ns).text or "").strip().replace("\n", " ")
        summary = (entry.find("atom:summary", ns).text or "").strip().replace("\n", " ")
        authors = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)]
        published = (entry.find("atom:published", ns).text or "")[:10]
        arxiv_id = (entry.find("atom:id", ns).text or "").split("/abs/")[-1]
        categories = [c.get("term", "") for c in entry.findall("arxiv:primary_category", ns)]
        if not categories:
            categories = [c.get("term", "") for c in entry.findall("atom:category", ns)]

        results.append({
            "title": title,
            "arxiv_id": arxiv_id,
            "doi": "",
            "year": int(published[:4]) if published else None,
            "authors": authors[:10],
            "abstract": summary[:500],
            "categories": categories[:5],
            "published": published,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "source_type": "grey_literature",
            "warning": "PREPRINT - NÃO peer-reviewed. Usar apenas como fonte suplementar.",
        })
    return json.dumps({"total_results": len(results), "results": results, "source_warning": "ArXiv é literatura cinzenta. Todos os resultados são preprints não revisados por pares."}, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_tavily(
    query: str,
    search_depth: str = "basic",
    include_domains: Optional[str] = None,
    max_results: int = 10,
) -> str:
    """Web search via Tavily for academic content, reports, policy documents. 
    include_domains: comma-separated domains (e.g. 'scholar.google.com,researchgate.net')"""
    if not TAVILY_API_KEY:
        return json.dumps({"error": "TAVILY_API_KEY not configured in .env"})

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": search_depth,
        "max_results": min(max_results, 20),
    }
    if include_domains:
        payload["include_domains"] = [d.strip() for d in include_domains.split(",")]

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.post("https://api.tavily.com/search", json=payload)
        resp.raise_for_status()
        data = resp.json()

    results = [{
        "title": r.get("title", ""),
        "url": r.get("url", ""),
        "content": r.get("content", "")[:300],
        "score": r.get("score", 0),
        "source_type": "web_search",
    } for r in data.get("results", [])]
    return json.dumps({"results": results}, ensure_ascii=False, indent=2)


# ============================================================
# GROUP 2: Paper Metadata (4 tools)
# ============================================================

@mcp.tool()
async def get_paper_by_doi(doi: str) -> str:
    """Get full metadata for a paper by DOI. Tries OpenAlex first, falls back to CrossRef."""
    doi = doi.strip().replace("https://doi.org/", "")
    # Try OpenAlex
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(f"{OPENALEX_BASE}/works/https://doi.org/{doi}", params={"mailto": POLITE_EMAIL})
            if resp.status_code == 200:
                work = resp.json()
                result = _format_openalex_work(work)
                result["references"] = [r.replace("https://openalex.org/", "") for r in (work.get("referenced_works") or [])[:50]]
                result["cited_by_api_url"] = work.get("cited_by_api_url", "")
                return json.dumps({"source": "openalex", "paper": result}, ensure_ascii=False, indent=2)
        except Exception:
            pass
        # Fallback CrossRef
        headers = {"User-Agent": f"BXScholar/1.0 (mailto:{POLITE_EMAIL})"}
        resp = await client.get(f"{CROSSREF_BASE}/works/{doi}", headers=headers)
        if resp.status_code == 200:
            item = resp.json().get("message", {})
            result = {
                "title": (item.get("title") or [""])[0],
                "doi": item.get("DOI", ""),
                "year": (item.get("published-print") or item.get("published-online") or {}).get("date-parts", [[None]])[0][0],
                "authors": [f"{a.get('given', '')} {a.get('family', '')}" for a in (item.get("author") or [])[:10]],
                "cited_by_count": item.get("is-referenced-by-count", 0),
                "journal": (item.get("container-title") or [""])[0],
                "issn": (item.get("ISSN") or [""])[0],
                "references_count": item.get("reference-count", 0),
            }
            return json.dumps({"source": "crossref", "paper": result}, ensure_ascii=False, indent=2)
    return json.dumps({"error": f"Paper not found for DOI: {doi}"})


@mcp.tool()
async def get_paper_citations(
    doi: str,
    direction: Literal["citing", "references"] = "citing",
    per_page: int = 25,
) -> str:
    """Get papers that cite this paper (citing) or papers cited by it (references). Essential for snowballing."""
    doi = doi.strip().replace("https://doi.org/", "")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # First get the OpenAlex ID for this DOI
        resp_meta = await client.get(f"{OPENALEX_BASE}/works/https://doi.org/{doi}", params={"mailto": POLITE_EMAIL, "select": "id,referenced_works,cited_by_api_url"})
        if resp_meta.status_code != 200:
            return json.dumps({"error": f"Paper not found: {doi}"})
        work_meta = resp_meta.json()
        openalex_id = work_meta.get("id", "").replace("https://openalex.org/", "")

        if direction == "citing":
            # Use the cited_by_api_url or cites filter with OpenAlex ID
            cited_by_url = work_meta.get("cited_by_api_url", "")
            if cited_by_url:
                resp = await client.get(cited_by_url, params={"per_page": min(per_page, 50), "sort": "cited_by_count:desc", "mailto": POLITE_EMAIL})
            else:
                params = {"filter": f"cites:{openalex_id}", "per_page": min(per_page, 50), "sort": "cited_by_count:desc", "mailto": POLITE_EMAIL}
                resp = await client.get(f"{OPENALEX_BASE}/works", params=params)
        else:
            ref_ids = (work_meta.get("referenced_works") or [])[:per_page]
            if not ref_ids:
                return json.dumps({"direction": direction, "count": 0, "results": []})
            pipe = "|".join(ref_ids)
            resp = await client.get(f"{OPENALEX_BASE}/works", params={"filter": f"openalex_id:{pipe}", "per_page": min(per_page, 50), "mailto": POLITE_EMAIL})

        resp.raise_for_status()
        data = resp.json()
        results = [_format_openalex_work(w) for w in data.get("results", [])]
        return json.dumps({"direction": direction, "doi": doi, "count": len(results), "results": results}, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_author_works(
    author_name: str,
    per_page: int = 20,
) -> str:
    """Get all works by an author, sorted by citation count. Useful for finding key researchers in a field."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Search author
        resp = await client.get(f"{OPENALEX_BASE}/authors", params={"search": author_name, "mailto": POLITE_EMAIL})
        resp.raise_for_status()
        authors = resp.json().get("results", [])
        if not authors:
            return json.dumps({"error": f"Author not found: {author_name}"})
        author = authors[0]
        author_id = author["id"]
        # Get works
        resp = await client.get(f"{OPENALEX_BASE}/works", params={
            "filter": f"author.id:{author_id}",
            "sort": "cited_by_count:desc",
            "per_page": min(per_page, 50),
            "mailto": POLITE_EMAIL,
        })
        resp.raise_for_status()
        works = [_format_openalex_work(w) for w in resp.json().get("results", [])]
        return json.dumps({
            "author": {"name": author.get("display_name"), "id": author_id, "works_count": author.get("works_count"), "cited_by_count": author.get("cited_by_count"), "h_index": (author.get("summary_stats") or {}).get("h_index")},
            "works": works,
        }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_journal_info(issn_or_name: str) -> str:
    """Get journal metadata including impact metrics, scope, and rankings (SJR/Qualis)."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        if "-" in issn_or_name and len(issn_or_name) <= 10:
            params = {"filter": f"issn:{issn_or_name}", "mailto": POLITE_EMAIL}
        else:
            params = {"search": issn_or_name, "mailto": POLITE_EMAIL}
        resp = await client.get(f"{OPENALEX_BASE}/sources", params=params)
        resp.raise_for_status()
        sources = resp.json().get("results", [])
        if not sources:
            return json.dumps({"error": f"Journal not found: {issn_or_name}"})
        src = sources[0]
        issn_l = src.get("issn_l", "")
        result = {
            "name": src.get("display_name", ""),
            "issn_l": issn_l,
            "issns": src.get("issn", []),
            "works_count": src.get("works_count"),
            "cited_by_count": src.get("cited_by_count"),
            "h_index": (src.get("summary_stats") or {}).get("h_index"),
            "type": src.get("type", ""),
            "publisher": (src.get("host_organization_lineage_names") or [""])[0] if src.get("host_organization_lineage_names") else "",
            "subjects": [c.get("display_name", "") for c in (src.get("x_concepts") or [])[:5]],
            "is_open_access": src.get("is_oa", False),
        }
        # Add SJR ranking
        sjr_info = _sjr_index.get(issn_l, {})
        if not sjr_info:
            for issn in (src.get("issn") or []):
                sjr_info = _sjr_index.get(issn, {})
                if sjr_info:
                    break
        result["sjr"] = sjr_info.get("sjr", "N/A")
        result["sjr_quartile"] = sjr_info.get("sjr_quartile", "N/A")
        # Add Qualis ranking
        qualis_info = _qualis_index.get(issn_l, {})
        if not qualis_info:
            for issn in (src.get("issn") or []):
                qualis_info = _qualis_index.get(issn, {})
                if qualis_info:
                    break
        result["qualis"] = qualis_info.get("qualis", "N/A")
        result["qualis_area"] = qualis_info.get("area", "N/A")
        return json.dumps(result, ensure_ascii=False, indent=2)


# ============================================================
# GROUP 3: Journal Rankings (3 tools)
# ============================================================

@mcp.tool()
async def lookup_journal_ranking(issn_or_name: str) -> str:
    """Look up journal ranking in local SJR + Qualis databases. Fast local lookup (no API call)."""
    issn = issn_or_name.strip()
    # Try direct ISSN lookup
    sjr_info = _sjr_index.get(issn, {})
    qualis_info = _qualis_index.get(issn, {})
    # Try name lookup if ISSN not found
    if not sjr_info and not qualis_info and len(issn) > 10:
        name_lower = issn.lower()
        # Search SJR by name
        found_issn = _sjr_by_name.get(name_lower)
        if found_issn:
            if len(found_issn) == 8:
                found_issn = f"{found_issn[:4]}-{found_issn[4:]}"
            sjr_info = _sjr_index.get(found_issn, {})
            qualis_info = _qualis_index.get(found_issn, {})
        else:
            # Partial match
            for title, stored_issn in _sjr_by_name.items():
                if name_lower in title or title in name_lower:
                    if len(stored_issn) == 8:
                        stored_issn = f"{stored_issn[:4]}-{stored_issn[4:]}"
                    sjr_info = _sjr_index.get(stored_issn, {})
                    qualis_info = _qualis_index.get(stored_issn, {})
                    break

    if not sjr_info and not qualis_info:
        return json.dumps({"error": f"Journal not found in local rankings: {issn_or_name}", "suggestion": "Try using get_journal_info for OpenAlex lookup"})

    return json.dumps({
        "query": issn_or_name,
        "sjr": {
            "title": sjr_info.get("title", "N/A"),
            "sjr_score": sjr_info.get("sjr", "N/A"),
            "quartile": sjr_info.get("sjr_quartile", "N/A"),
            "h_index": sjr_info.get("h_index", "N/A"),
            "area": sjr_info.get("area", "N/A"),
        },
        "qualis": {
            "title": qualis_info.get("title", "N/A"),
            "classification": qualis_info.get("qualis", "N/A"),
            "area": qualis_info.get("area", "N/A"),
        },
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_top_journals_for_field(
    field: str,
    limit: int = 20,
) -> str:
    """Get top-ranked journals for a research field based on SJR score. Returns journals sorted by SJR descending."""
    field_lower = field.lower()
    matches = []
    for issn, info in _sjr_index.items():
        area = str(info.get("area", "")).lower()
        title = str(info.get("title", "")).lower()
        if field_lower in area or field_lower in title:
            try:
                sjr_val = float(str(info.get("sjr", "0")).replace(",", "."))
            except (ValueError, TypeError):
                sjr_val = 0
            matches.append({
                "title": info.get("title"),
                "issn": issn,
                "sjr": info.get("sjr"),
                "quartile": info.get("sjr_quartile"),
                "h_index": info.get("h_index"),
                "qualis": _qualis_index.get(issn, {}).get("qualis", "N/A"),
                "_sjr_num": sjr_val,
            })
    matches.sort(key=lambda x: x["_sjr_num"], reverse=True)
    for m in matches:
        del m["_sjr_num"]
    return json.dumps({"field": field, "top_journals": matches[:limit]}, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_journal_papers(
    issn: str,
    query: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    per_page: int = 25,
) -> str:
    """Search for papers within a SPECIFIC journal by ISSN. Essential for finding papers from the target journal."""
    filters = [f"primary_location.source.issn:{issn}"]
    if year_from:
        filters.append(f"publication_year:>{year_from - 1}")
    if year_to:
        filters.append(f"publication_year:<{year_to + 1}")

    params = {
        "filter": ",".join(filters),
        "sort": "cited_by_count:desc",
        "per_page": min(per_page, 50),
        "mailto": POLITE_EMAIL,
    }
    if query:
        params["search"] = query

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(f"{OPENALEX_BASE}/works", params=params)
        resp.raise_for_status()
        data = resp.json()

    results = [_format_openalex_work(w) for w in data.get("results", [])]
    total = data.get("meta", {}).get("count", 0)
    return json.dumps({"journal_issn": issn, "total_results": total, "returned": len(results), "results": results}, ensure_ascii=False, indent=2)


# ============================================================
# GROUP 4: Bibliometrics (3 tools)
# ============================================================

@mcp.tool()
async def build_citation_network(
    seed_dois: str,
    depth: int = 1,
    max_nodes: int = 100,
) -> str:
    """Build a citation network from seed DOIs. seed_dois: comma-separated DOIs. depth: 1 or 2 levels. Returns nodes and edges."""
    dois = [d.strip().replace("https://doi.org/", "") for d in seed_dois.split(",")]
    depth = min(depth, 2)
    max_nodes = min(max_nodes, 200)

    nodes = {}
    edges = []
    to_process = [(doi, 0) for doi in dois]

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        while to_process and len(nodes) < max_nodes:
            doi, level = to_process.pop(0)
            if doi in nodes:
                continue
            try:
                resp = await client.get(f"{OPENALEX_BASE}/works/https://doi.org/{doi}", params={"mailto": POLITE_EMAIL})
                if resp.status_code != 200:
                    continue
                work = resp.json()
                node = _format_openalex_work(work)
                node["level"] = level
                nodes[doi] = node
                if level < depth:
                    refs = (work.get("referenced_works") or [])[:10]
                    for ref_id in refs:
                        # Get DOI for referenced work
                        try:
                            ref_resp = await client.get(f"{OPENALEX_BASE}/works/{ref_id}", params={"mailto": POLITE_EMAIL})
                            if ref_resp.status_code == 200:
                                ref_work = ref_resp.json()
                                ref_doi = (ref_work.get("doi") or "").replace("https://doi.org/", "")
                                if ref_doi:
                                    edges.append({"from": doi, "to": ref_doi, "type": "cites"})
                                    if ref_doi not in nodes and len(nodes) < max_nodes:
                                        to_process.append((ref_doi, level + 1))
                        except Exception:
                            continue
            except Exception:
                continue

    return json.dumps({
        "nodes_count": len(nodes),
        "edges_count": len(edges),
        "nodes": list(nodes.values()),
        "edges": edges[:200],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def find_co_citation_clusters(
    dois: str,
    min_co_citations: int = 2,
) -> str:
    """Find co-citation clusters: which pairs of papers are frequently cited together. dois: comma-separated DOIs."""
    doi_list = [d.strip().replace("https://doi.org/", "") for d in dois.split(",")]
    if len(doi_list) < 2:
        return json.dumps({"error": "Need at least 2 DOIs"})

    citing_sets = {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        for doi in doi_list[:20]:  # Limit to 20 to avoid API abuse
            try:
                resp = await client.get(f"{OPENALEX_BASE}/works", params={
                    "filter": f"cites:https://doi.org/{doi}",
                    "per_page": 50,
                    "select": "id",
                    "mailto": POLITE_EMAIL,
                })
                if resp.status_code == 200:
                    citers = {w["id"] for w in resp.json().get("results", [])}
                    citing_sets[doi] = citers
            except Exception:
                continue

    # Find co-citation pairs
    pairs = []
    doi_keys = list(citing_sets.keys())
    for i in range(len(doi_keys)):
        for j in range(i + 1, len(doi_keys)):
            shared = citing_sets[doi_keys[i]] & citing_sets[doi_keys[j]]
            if len(shared) >= min_co_citations:
                pairs.append({
                    "paper_a": doi_keys[i],
                    "paper_b": doi_keys[j],
                    "co_citations": len(shared),
                })
    pairs.sort(key=lambda x: x["co_citations"], reverse=True)
    return json.dumps({"co_citation_pairs": pairs[:50], "total_pairs": len(pairs)}, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_keyword_trends(
    keywords: str,
    year_from: int = 2015,
    year_to: int = 2025,
) -> str:
    """Track keyword frequency in academic publications over time. keywords: comma-separated. Returns yearly counts per keyword."""
    kw_list = [k.strip() for k in keywords.split(",")]
    trends = {}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for kw in kw_list[:5]:  # Max 5 keywords
            yearly = {}
            for year in range(year_from, year_to + 1):
                try:
                    resp = await client.get(f"{OPENALEX_BASE}/works", params={
                        "filter": f"default.search:{kw},publication_year:{year}",
                        "per_page": 1,
                        "mailto": POLITE_EMAIL,
                    })
                    if resp.status_code == 200:
                        yearly[str(year)] = resp.json().get("meta", {}).get("count", 0)
                except Exception:
                    yearly[str(year)] = 0
            trends[kw] = yearly

    return json.dumps({"keyword_trends": trends}, ensure_ascii=False, indent=2)


# ============================================================
# GROUP 5: Citation Verification (3 tools)
# ============================================================

@mcp.tool()
async def verify_citation(
    author: str,
    year: int,
    title_fragment: str,
) -> str:
    """Verify if a citation exists. Anti-hallucination tool. Returns verified status and closest match."""
    headers = {"User-Agent": f"BXScholar/1.0 (mailto:{POLITE_EMAIL})"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # Try CrossRef bibliographic search
        resp = await client.get(f"{CROSSREF_BASE}/works", params={
            "query.bibliographic": f"{author} {title_fragment}",
            "filter": f"from-pub-date:{year - 1},until-pub-date:{year + 1}",
            "rows": 5,
        }, headers=headers)
        if resp.status_code != 200:
            return json.dumps({"verified": False, "error": "CrossRef API error"})

        items = resp.json().get("message", {}).get("items", [])
        if not items:
            # Fallback to OpenAlex
            resp2 = await client.get(f"{OPENALEX_BASE}/works", params={
                "search": f"{author} {title_fragment}",
                "filter": f"publication_year:{year}",
                "per_page": 5,
                "mailto": POLITE_EMAIL,
            })
            if resp2.status_code == 200:
                oa_results = resp2.json().get("results", [])
                if oa_results:
                    best = oa_results[0]
                    return json.dumps({
                        "verified": True,
                        "source": "openalex",
                        "confidence": "medium",
                        "match": {
                            "title": best.get("title", ""),
                            "doi": (best.get("doi") or "").replace("https://doi.org/", ""),
                            "year": best.get("publication_year"),
                            "authors": [a.get("author", {}).get("display_name", "") for a in (best.get("authorships") or [])[:5]],
                        },
                    }, ensure_ascii=False, indent=2)
            return json.dumps({"verified": False, "query": {"author": author, "year": year, "title": title_fragment}, "message": "No match found in CrossRef or OpenAlex. This citation may be fabricated."})

        best = items[0]
        # Check if match is plausible
        best_title = (best.get("title") or [""])[0].lower()
        query_title = title_fragment.lower()
        title_match = query_title in best_title or best_title in query_title or len(set(query_title.split()) & set(best_title.split())) > 2

        return json.dumps({
            "verified": title_match,
            "source": "crossref",
            "confidence": "high" if title_match else "low",
            "match": {
                "title": (best.get("title") or [""])[0],
                "doi": best.get("DOI", ""),
                "year": (best.get("published-print") or best.get("published-online") or {}).get("date-parts", [[None]])[0][0],
                "authors": [f"{a.get('given', '')} {a.get('family', '')}" for a in (best.get("author") or [])[:5]],
                "journal": (best.get("container-title") or [""])[0],
            },
        }, ensure_ascii=False, indent=2)


@mcp.tool()
async def check_retraction(doi: str) -> str:
    """Check if a paper has been retracted. Always verify before citing."""
    doi = doi.strip().replace("https://doi.org/", "")
    headers = {"User-Agent": f"BXScholar/1.0 (mailto:{POLITE_EMAIL})"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(f"{CROSSREF_BASE}/works/{doi}", headers=headers)
        if resp.status_code != 200:
            return json.dumps({"doi": doi, "retracted": "unknown", "error": "Could not fetch from CrossRef"})
        item = resp.json().get("message", {})
        # Check for retraction
        updates = item.get("update-to") or []
        retracted = any(u.get("type") == "retraction" for u in updates)
        # Also check if this IS a retraction notice
        is_retraction_notice = item.get("type") == "retraction"
        return json.dumps({
            "doi": doi,
            "retracted": retracted,
            "is_retraction_notice": is_retraction_notice,
            "updates": [{"type": u.get("type"), "doi": u.get("DOI")} for u in updates] if updates else [],
            "title": (item.get("title") or [""])[0],
        }, ensure_ascii=False, indent=2)


@mcp.tool()
async def batch_verify_references(
    references_json: str,
) -> str:
    """Verify a batch of references. Input: JSON array of {author, year, title} objects.
    Example: [{"author": "Simon", "year": 1955, "title": "behavioral model rational choice"}]"""
    try:
        refs = json.loads(references_json)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON input. Expected array of {author, year, title} objects."})

    results = []
    headers = {"User-Agent": f"BXScholar/1.0 (mailto:{POLITE_EMAIL})"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        for ref in refs[:30]:  # Max 30 refs per batch
            author = ref.get("author", "")
            year = ref.get("year", 2000)
            title = ref.get("title", "")
            try:
                resp = await client.get(f"{CROSSREF_BASE}/works", params={
                    "query.bibliographic": f"{author} {title}",
                    "filter": f"from-pub-date:{year - 1},until-pub-date:{year + 1}",
                    "rows": 1,
                }, headers=headers)
                if resp.status_code == 200:
                    items = resp.json().get("message", {}).get("items", [])
                    if items:
                        best = items[0]
                        best_title = (best.get("title") or [""])[0].lower()
                        query_title = title.lower()
                        matched = query_title in best_title or best_title in query_title or len(set(query_title.split()) & set(best_title.split())) > 2
                        results.append({
                            "query": ref,
                            "verified": matched,
                            "doi": best.get("DOI", "") if matched else "",
                            "matched_title": (best.get("title") or [""])[0],
                        })
                    else:
                        results.append({"query": ref, "verified": False, "doi": "", "matched_title": ""})
                else:
                    results.append({"query": ref, "verified": False, "error": f"HTTP {resp.status_code}"})
            except Exception as e:
                results.append({"query": ref, "verified": False, "error": str(e)})
            await asyncio.sleep(0.1)  # Rate limiting

    verified_count = sum(1 for r in results if r.get("verified"))
    return json.dumps({
        "total": len(results),
        "verified": verified_count,
        "unverified": len(results) - verified_count,
        "results": results,
    }, ensure_ascii=False, indent=2)




# ============================================================
# GROUP 6: Full-Text Pipeline (3 tools)
# ============================================================

@mcp.tool()
async def check_open_access(doi: str) -> str:
    """Check if a paper has Open Access full-text available via Unpaywall.
    Returns OA status and PDF URL if available."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            resp = await client.get(
                f"https://api.unpaywall.org/v2/{doi}",
                params={"email": POLITE_EMAIL},
            )
            if resp.status_code == 404:
                return json.dumps({"doi": doi, "oa_status": "not_found", "message": "DOI not found in Unpaywall"})
            resp.raise_for_status()
            data = resp.json()

            result = {
                "doi": doi,
                "title": data.get("title", ""),
                "oa_status": data.get("oa_status", "closed"),
                "is_oa": data.get("is_oa", False),
                "journal": data.get("journal_name", ""),
                "publisher": data.get("publisher", ""),
            }

            best_loc = data.get("best_oa_location")
            if best_loc:
                result["pdf_url"] = best_loc.get("url_for_pdf") or best_loc.get("url")
                result["version"] = best_loc.get("version", "unknown")
                result["license"] = best_loc.get("license") or "unknown"
                result["host_type"] = best_loc.get("host_type", "unknown")

            return json.dumps(result, ensure_ascii=False, indent=2)
        except httpx.HTTPStatusError as e:
            return json.dumps({"doi": doi, "error": f"HTTP {e.response.status_code}"})
        except Exception as e:
            return json.dumps({"doi": doi, "error": str(e)})


@mcp.tool()
async def download_pdf(url: str, save_path: str) -> str:
    """Download a PDF from a URL (typically an OA source) and save to local path.
    Creates parent directories if needed. Returns the saved file path."""
    save = Path(save_path).expanduser()
    save.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers={
                "User-Agent": f"BX-Scholar/1.0 (mailto:{POLITE_EMAIL})",
                "Accept": "application/pdf",
            })
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "pdf" not in content_type and not save.suffix == ".pdf":
                return json.dumps({"error": f"Response is not a PDF (content-type: {content_type})", "url": url})

            save.write_bytes(resp.content)
            size_mb = len(resp.content) / (1024 * 1024)
            return json.dumps({
                "saved_to": str(save),
                "size_mb": round(size_mb, 2),
                "url": url,
            })
        except Exception as e:
            return json.dumps({"error": str(e), "url": url})


@mcp.tool()
async def extract_pdf_text(pdf_path: str, output_format: str = "markdown") -> str:
    """Extract text from a PDF file and return as markdown or plain text.
    Uses marker-pdf (ML-powered) for high-quality extraction with pymupdf fallback.
    output_format: 'markdown' (structured with headers) or 'text' (plain)."""
    path = Path(pdf_path).expanduser()
    if not path.exists():
        return json.dumps({"error": f"File not found: {pdf_path}"})

    full_text = ""
    method_used = "unknown"
    num_pages = 0

    # Try marker-pdf first (better quality for academic papers)
    if output_format == "markdown":
        try:
            from marker.converters.pdf import PdfConverter
            from marker.config.parser import ConfigParser
            config = ConfigParser({"output_format": "markdown"})
            converter = PdfConverter(config=config)
            result = converter(str(path))
            full_text = result.markdown
            num_pages = result.metadata.get("pages", 0) if hasattr(result, "metadata") and isinstance(result.metadata, dict) else 0
            method_used = "marker-pdf"
        except Exception as marker_err:
            print(f"[WARN] marker-pdf failed: {marker_err}, falling back to pymupdf", file=sys.stderr)
            method_used = "pymupdf_fallback"

    # Fallback: pymupdf (always used for 'text' format, or if marker fails)
    if not full_text:
        try:
            import fitz
            doc = fitz.open(str(path))
            num_pages = len(doc)
            pages = []
            for page in doc:
                if output_format == "markdown" or method_used == "pymupdf_fallback":
                    blocks = page.get_text("dict")["blocks"]
                    page_text = []
                    for block in blocks:
                        if block["type"] == 0:
                            for line in block.get("lines", []):
                                spans = line.get("spans", [])
                                if not spans:
                                    continue
                                text = "".join(s["text"] for s in spans).strip()
                                if not text:
                                    continue
                                max_size = max(s["size"] for s in spans)
                                is_bold = any(("bold" in s.get("font", "").lower() or
                                              s.get("flags", 0) & 16) for s in spans)
                                if max_size > 14 and is_bold:
                                    page_text.append(f"\n## {text}\n")
                                elif max_size > 12 and is_bold:
                                    page_text.append(f"\n### {text}\n")
                                elif is_bold and len(text) < 100:
                                    page_text.append(f"\n**{text}**\n")
                                else:
                                    page_text.append(text)
                    pages.append("\n".join(page_text))
                else:
                    pages.append(page.get_text())
            doc.close()
            full_text = "\n\n---\n\n".join(pages)
            if method_used == "unknown":
                method_used = "pymupdf"
        except Exception as e:
            return json.dumps({"error": str(e), "file": str(path)})

    # Truncate if too long
    if len(full_text) > 100000:
        full_text = full_text[:100000] + "\n\n[... TRUNCATED — full text is too long. Process in chunks.]"

    return json.dumps({
        "file": str(path),
        "pages": num_pages,
        "chars": len(full_text),
        "format": output_format,
        "method": method_used,
        "text": full_text,
    }, ensure_ascii=False)


# ============================================================
# GROUP 7: SciELO (1 tool)
# ============================================================

@mcp.tool()
async def search_scielo(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    lang: str = "en",
    max_results: int = 20,
) -> str:
    """Search SciELO for Brazilian/Latin American Open Access papers.
    All SciELO papers are Open Access with full PDFs available.
    Great for Brazilian journals: RAE, RAP, BAR, RAUSP, etc."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            # SciELO search via their API
            params = {
                "q": query,
                "count": min(max_results, 50),
                "output": "json",
            }
            if year_from:
                params["filter[year_cluster][]"] = list(range(year_from, (year_to or 2026) + 1))

            # Try SciELO search API
            resp = await client.get(
                "https://search.scielo.org/",
                params={"q": query, "output": "json", "count": min(max_results, 50), "lang": lang},
            )

            # Fallback: use OpenAlex with SciELO host filter
            if resp.status_code != 200 or "application/json" not in resp.headers.get("content-type", ""):
                # Use OpenAlex filtered to SciELO-hosted journals
                oa_params = {
                    "search": query,
                    "filter": "host_venue.publisher:SciELO",
                    "per_page": min(max_results, 50),
                    "mailto": POLITE_EMAIL,
                }
                if year_from:
                    oa_params["filter"] += f",publication_year:>{year_from - 1}"
                if year_to:
                    oa_params["filter"] += f",publication_year:<{year_to + 1}"

                resp2 = await client.get(f"{OPENALEX_BASE}/works", params=oa_params)
                resp2.raise_for_status()
                data = resp2.json()
                results = []
                for work in data.get("results", []):
                    r = _format_openalex_work(work)
                    r["source"] = "scielo_via_openalex"
                    r["full_text_available"] = True
                    oa_url = work.get("open_access", {}).get("oa_url")
                    if oa_url:
                        r["pdf_url"] = oa_url
                    results.append(r)
                return json.dumps({
                    "query": query,
                    "source": "openalex_scielo_filter",
                    "total": data.get("meta", {}).get("count", 0),
                    "returned": len(results),
                    "results": results,
                    "note": "All SciELO papers are Open Access — PDFs available",
                }, ensure_ascii=False, indent=2)

            # Parse direct SciELO response
            data = resp.json()
            results = []
            for doc in data.get("docs", data.get("results", []))[:max_results]:
                results.append({
                    "title": doc.get("title", [""])[0] if isinstance(doc.get("title"), list) else doc.get("title", ""),
                    "authors": doc.get("au", []),
                    "year": doc.get("year_cluster", [""])[0] if isinstance(doc.get("year_cluster"), list) else doc.get("year_cluster", ""),
                    "journal": doc.get("journal_title", [""])[0] if isinstance(doc.get("journal_title"), list) else doc.get("journal_title", ""),
                    "doi": doc.get("doi", ""),
                    "url": doc.get("id", ""),
                    "lang": doc.get("la", []),
                    "source": "scielo",
                    "full_text_available": True,
                })
            return json.dumps({
                "query": query,
                "source": "scielo_direct",
                "returned": len(results),
                "results": results,
                "note": "All SciELO papers are Open Access — PDFs available",
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e), "query": query})


# ============================================================
# GROUP 8: Semantic Scholar (3 tools)
# ============================================================

SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1"
S2_API_KEY = os.getenv("S2_API_KEY", "")
_last_s2_call = 0.0


def _s2_headers():
    """Headers for Semantic Scholar API. Includes API key if available."""
    headers = {"User-Agent": f"BX-Scholar/1.0 (mailto:{POLITE_EMAIL})"}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY
    return headers


async def _s2_rate_limit():
    """Rate limit: 1 req/s without key, 10 req/s with key."""
    global _last_s2_call
    now = time.time()
    delay = 0.15 if S2_API_KEY else 1.5
    elapsed = now - _last_s2_call
    if elapsed < delay:
        await asyncio.sleep(delay - elapsed)
    _last_s2_call = time.time()


async def _s2_request(client, url, params, max_retries=3):
    """Make S2 API request with retry on 429. Returns None on persistent 429."""
    resp = None
    for attempt in range(max_retries):
        await _s2_rate_limit()
        resp = await client.get(url, params=params, headers=_s2_headers())
        if resp.status_code == 429:
            wait = (attempt + 1) * 5
            print(f"[WARN] S2 429 — retry {attempt+1}/{max_retries} in {wait}s", file=sys.stderr)
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    return None  # persistent 429


@mcp.tool()
async def search_semantic_scholar(
    query: str,
    year: Optional[str] = None,
    fields_of_study: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Search Semantic Scholar for papers. Returns TLDR summaries and influential citation counts.
    year: e.g. '2020-2024' or '2023-'. fields_of_study: e.g. 'Computer Science', 'Political Science'."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            params = {
                "query": query,
                "limit": min(limit, 100),
                "fields": "title,authors,year,venue,externalIds,citationCount,influentialCitationCount,tldr,abstract,publicationTypes,journal,openAccessPdf",
            }
            if year:
                params["year"] = year
            if fields_of_study:
                params["fieldsOfStudy"] = fields_of_study

            resp = await _s2_request(client, f"{SEMANTIC_SCHOLAR_BASE}/paper/search", params)
            if resp is None:
                return json.dumps({"error": "Semantic Scholar rate limited (429). Set S2_API_KEY in .env for higher limits. Get free key: https://www.semanticscholar.org/product/api#api-key-form", "query": query})
            data = resp.json()

            results = []
            for paper in data.get("data", []):
                authors = [a.get("name", "") for a in paper.get("authors", [])[:10]]
                ext_ids = paper.get("externalIds", {})
                tldr = paper.get("tldr")
                journal = paper.get("journal") or {}

                r = {
                    "title": paper.get("title", ""),
                    "authors": authors,
                    "year": paper.get("year"),
                    "venue": paper.get("venue", ""),
                    "journal": journal.get("name", "") if isinstance(journal, dict) else str(journal),
                    "doi": ext_ids.get("DOI", ""),
                    "s2_id": paper.get("paperId", ""),
                    "arxiv_id": ext_ids.get("ArXiv", ""),
                    "citation_count": paper.get("citationCount", 0),
                    "influential_citation_count": paper.get("influentialCitationCount", 0),
                    "tldr": tldr.get("text", "") if tldr else "",
                    "publication_types": paper.get("publicationTypes", []),
                    "source": "semantic_scholar",
                }
                oa_pdf = paper.get("openAccessPdf")
                if oa_pdf:
                    r["pdf_url"] = oa_pdf.get("url", "")
                results.append(r)

            return json.dumps({
                "query": query,
                "total": data.get("total", 0),
                "returned": len(results),
                "results": results,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e), "query": query})


@mcp.tool()
async def get_influential_citations(doi_or_s2id: str, limit: int = 20) -> str:
    """Get influential citations of a paper — citations where the citing paper
    substantially engages with this work (not just incidental mentions).
    Accepts DOI (prefixed with 'DOI:') or Semantic Scholar paper ID."""
    paper_id = f"DOI:{doi_or_s2id}" if "/" in doi_or_s2id and not doi_or_s2id.startswith("DOI:") else doi_or_s2id

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            resp = await _s2_request(client,
                f"{SEMANTIC_SCHOLAR_BASE}/paper/{paper_id}/citations",
                {
                    "fields": "title,authors,year,venue,citationCount,influentialCitationCount,isInfluential,contexts,intents,externalIds",
                    "limit": min(limit, 100),
                },
            )
            if resp is None:
                return json.dumps({"error": "Semantic Scholar rate limited. Set S2_API_KEY in .env.", "paper": doi_or_s2id})
            data = resp.json()

            results = []
            for item in data.get("data", []):
                citing = item.get("citingPaper", {})
                if not citing.get("title"):
                    continue
                authors = [a.get("name", "") for a in citing.get("authors", [])[:5]]
                ext_ids = citing.get("externalIds", {})
                results.append({
                    "title": citing.get("title", ""),
                    "authors": authors,
                    "year": citing.get("year"),
                    "venue": citing.get("venue", ""),
                    "doi": ext_ids.get("DOI", ""),
                    "citation_count": citing.get("citationCount", 0),
                    "is_influential": item.get("isInfluential", False),
                    "intents": item.get("intents", []),
                    "contexts": item.get("contexts", [])[:3],  # Max 3 context snippets
                })

            influential = [r for r in results if r["is_influential"]]
            return json.dumps({
                "paper": doi_or_s2id,
                "total_citations_returned": len(results),
                "influential_count": len(influential),
                "citations": results,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e), "paper": doi_or_s2id})


@mcp.tool()
async def get_citation_context(citing_doi: str, cited_doi: str) -> str:
    """Get the exact text snippets where one paper cites another.
    Useful for understanding HOW a paper is cited (background, method, result).
    Both parameters accept DOIs."""
    citing_id = f"DOI:{citing_doi}" if "/" in citing_doi and not citing_doi.startswith("DOI:") else citing_doi

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            # Get all references of the citing paper with contexts
            resp = await _s2_request(client,
                f"{SEMANTIC_SCHOLAR_BASE}/paper/{citing_id}/references",
                {
                    "fields": "title,authors,year,externalIds,contexts,intents,isInfluential",
                    "limit": 500,
                },
            )
            if resp is None:
                return json.dumps({"error": "Semantic Scholar rate limited. Set S2_API_KEY in .env.", "citing_paper": citing_doi})
            data = resp.json()

            # Find the cited paper in references
            cited_doi_lower = cited_doi.lower().replace("doi:", "")
            for item in data.get("data", []):
                ref = item.get("citedPaper", {})
                ref_ids = ref.get("externalIds", {})
                ref_doi = (ref_ids.get("DOI") or "").lower()
                if ref_doi == cited_doi_lower or cited_doi_lower in ref_doi:
                    return json.dumps({
                        "citing_paper": citing_doi,
                        "cited_paper": cited_doi,
                        "cited_title": ref.get("title", ""),
                        "is_influential": item.get("isInfluential", False),
                        "intents": item.get("intents", []),
                        "contexts": item.get("contexts", []),
                    }, ensure_ascii=False, indent=2)

            return json.dumps({
                "citing_paper": citing_doi,
                "cited_paper": cited_doi,
                "found": False,
                "message": "Cited paper not found in references of citing paper",
            })
        except Exception as e:
            return json.dumps({"error": str(e)})


# ============================================================
# GROUP 9: Rankings Management (1 tool)
# ============================================================

@mcp.tool()
async def update_rankings(sjr_url: str = "", qualis_path: str = "") -> str:
    """Update journal rankings data.
    For SJR: downloads from scimagojr.com (may be blocked - provide direct URL if needed).
    For Qualis: provide local path to the XLSX file downloaded from Plataforma Sucupira.
    After updating, the server must be restarted to reload the data."""
    results = {}
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Update SJR
    sjr_path = DATA_DIR / "sjr_rankings.csv"
    if sjr_url:
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                resp = await client.get(sjr_url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                sjr_path.write_bytes(resp.content)
                results["sjr"] = f"Downloaded {len(resp.content)/(1024*1024):.1f}MB to {sjr_path}"
        except Exception as e:
            results["sjr"] = f"Download failed: {e}. Download manually from https://www.scimagojr.com/journalrank.php"
    else:
        # Try default URL
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                resp = await client.get("https://www.scimagojr.com/journalrank.php?out=xls",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                if resp.status_code == 200 and len(resp.content) > 1000000:
                    sjr_path.write_bytes(resp.content)
                    results["sjr"] = f"Downloaded {len(resp.content)/(1024*1024):.1f}MB"
                else:
                    results["sjr"] = f"Auto-download blocked (HTTP {resp.status_code}). Download manually from https://www.scimagojr.com/journalrank.php and save to {sjr_path}"
        except Exception as e:
            results["sjr"] = f"Failed: {e}. Download manually."

    # Update Qualis
    if qualis_path:
        qp = Path(qualis_path).expanduser()
        if qp.exists():
            dest = DATA_DIR / "qualis_capes.xlsx"
            shutil.copy2(qp, dest)
            results["qualis"] = f"Copied from {qp} to {dest}"
        else:
            results["qualis"] = f"File not found: {qualis_path}"
    else:
        results["qualis"] = "No Qualis file provided. Download from https://sucupira.capes.gov.br and provide the path."

    results["note"] = "Restart the MCP server to reload updated rankings."
    return json.dumps(results, ensure_ascii=False, indent=2)


# ============================================================
# MAIN
# ============================================================

def main():
    # Load ranking data at startup
    _load_sjr()
    _load_qualis()
    print(f"[INFO] BX-Scholar MCP Server starting with {len(_sjr_index)} SJR + {len(_qualis_index)} Qualis entries", file=sys.stderr)
    try:
        asyncio.run(mcp.run())
    except KeyboardInterrupt:
        print("\nServer stopped.", file=sys.stderr)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
