<div align="center">

# 🎓 BX-Scholar MCP Server

**Your AI agent's academic research toolkit**

Search 250M+ papers. Verify every citation. Never hallucinate a reference again.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-8B5CF6.svg)](https://modelcontextprotocol.io)
[![OpenAlex](https://img.shields.io/badge/OpenAlex-250M+_papers-E5543E.svg)](https://openalex.org)
[![arXiv](https://img.shields.io/badge/arXiv-Grey_Literature-B31B1B.svg)](https://arxiv.org)

[Features](#-features) · [Quick Start](#-quick-start) · [Configuration](#-configuration) · [Tools Reference](#-tools-reference) · [Prompts](#-mcp-prompts) · [Rankings](#-journal-rankings)

---

</div>

## Why BX-Scholar?

AI agents hallucinate references. They cite papers that don't exist, fabricate DOIs, and attribute quotes to wrong authors. **BX-Scholar fixes this** by giving your agent direct access to real academic databases with a built-in anti-hallucination pipeline.

```
Agent: "According to Smith et al. (2023)..."
BX-Scholar: verify_citation("Smith", 2023, "key words") → ❌ NOT FOUND
Agent: *removes citation instead of fabricating it*
```

### What makes it different

- **25 MCP tools** spanning the entire research lifecycle — from discovery to submission
- **Anti-hallucination** — verify every citation against CrossRef + OpenAlex before using it
- **Real journal rankings** — local SJR (32K+ journals) + Qualis CAPES (170K+ entries), not LLM guesses
- **Multi-source search** — OpenAlex, CrossRef, ArXiv, SciELO, Semantic Scholar, Tavily in one interface
- **Citation intelligence** — know which citations are influential vs. incidental (Semantic Scholar)
- **Full-text pipeline** — Unpaywall OA check → PDF download → text extraction (marker-pdf / PyMuPDF)
- **4 workflow prompts** — plug-and-play research protocols any agent can follow

---

## ✨ Features

<table>
<tr>
<td width="50%">

### 🔍 Literature Search
Search across 5+ academic databases simultaneously. Deduplicate by DOI. ArXiv results automatically flagged as grey literature.

### 📊 Journal Rankings
Local SJR + Qualis CAPES database. Instant lookups — no API calls. Find top journals in any field sorted by impact.

### 🔗 Bibliometrics
Build citation networks from seed papers. Find co-citation clusters. Track keyword trends over time.

### 📥 Full-Text Pipeline
Check Open Access via Unpaywall. Download PDFs. Extract text to markdown with ML-powered quality (marker-pdf).

</td>
<td width="50%">

### 🛡️ Citation Verification
**The killer feature.** Verify that every citation exists in CrossRef/OpenAlex before your agent uses it. Check for retractions. Batch verify entire reference lists.

### 🧠 Citation Intelligence
Semantic Scholar integration: TLDR summaries, influential citation counts, and exact citation context snippets. Know *how* papers cite each other.

### 📋 Research Prompts
Pre-built workflow prompts for systematic literature search, journal calibration, citation verification, and full research pipelines.

### 🇧🇷 Brazilian Research
SciELO integration for Brazilian/LATAM Open Access papers. Qualis CAPES rankings. Ready for RAE, RAP, BAR, RAUSP and more.

</td>
</tr>
</table>

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Install & Run

```bash
git clone https://github.com/leomcamilo/bx-scholar-mcp.git
cd bx-scholar-mcp
cp .env.example .env        # edit with your email
uv run run_server.py         # that's it
```

### First Run

On first start, the server will warn about missing ranking files. You have two options:

1. **Use the `update_rankings` tool** after the server starts (the agent can call it)
2. **Download manually** (see [Journal Rankings](#-journal-rankings))

The server works without ranking files — tools will return `"N/A"` for rankings data.

---

## ⚙️ Configuration

### Claude Desktop

Add to your config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "bx-scholar": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/bx-scholar-mcp", "run_server.py"],
      "env": {
        "POLITE_EMAIL": "your@email.com"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add bx-scholar -- uv run --directory /path/to/bx-scholar-mcp run_server.py
```

### Cursor / Windsurf

Add to `.cursor/mcp.json` or equivalent:

```json
{
  "mcpServers": {
    "bx-scholar": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/bx-scholar-mcp", "run_server.py"],
      "env": {
        "POLITE_EMAIL": "your@email.com"
      }
    }
  }
}
```

### OpenClaw

Add to `openclaw.json` under `mcp.servers`:

```json
"bx-scholar": {
  "command": "uv",
  "args": ["run", "python", "run_server.py"],
  "env": {
    "POLITE_EMAIL": "your@email.com",
    "TAVILY_API_KEY": "${TAVILY_API_KEY}",
    "S2_API_KEY": "${S2_API_KEY}"
  },
  "cwd": "/path/to/bx-scholar-mcp"
}
```

### Environment Variables

| Variable | Required | Free? | Description |
|----------|:--------:|:-----:|-------------|
| `POLITE_EMAIL` | **Yes** | ✅ | Your email — used for polite API pools (OpenAlex, CrossRef, Unpaywall) |
| `TAVILY_API_KEY` | No | Freemium | [Tavily](https://tavily.com) web search for policy docs & reports |
| `S2_API_KEY` | No | ✅ | [Semantic Scholar](https://www.semanticscholar.org/product/api#api-key-form) — higher rate limits |

```bash
cp .env.example .env
# Edit .env with your values
```

---

## 🔧 Tools Reference

### 🔍 Literature Search (5 tools)

| Tool | Source | Description |
|------|--------|-------------|
| `search_openalex` | OpenAlex | Search 250M+ papers. Filter by year, journal (ISSN), type. Sort by citations or date. |
| `search_crossref` | CrossRef | DOI verification and bibliographic metadata. Good for reference enrichment. |
| `search_arxiv` | ArXiv | Preprints search. **All results flagged as grey literature.** |
| `search_scielo` | SciELO | Brazilian/LATAM Open Access papers. Essential for BR journals. |
| `search_tavily` | Tavily | Web search for reports, policy documents, and grey literature. |

### 📄 Paper Metadata (4 tools)

| Tool | Description |
|------|-------------|
| `get_paper_by_doi` | Full metadata by DOI — OpenAlex first, CrossRef fallback |
| `get_paper_citations` | Forward snowballing (who cites this?) or backward (references) |
| `get_author_works` | Author profile: h-index, works sorted by citations |
| `get_journal_info` | Journal metadata + SJR quartile + Qualis classification |

### 📊 Journal Rankings (3 tools)

| Tool | Description |
|------|-------------|
| `lookup_journal_ranking` | **Instant** local lookup — SJR + Qualis by ISSN or name. No API call. |
| `get_top_journals_for_field` | Top journals in a field, sorted by SJR score |
| `get_journal_papers` | Search papers *within* a specific journal by ISSN |

### 🔗 Bibliometrics (3 tools)

| Tool | Description |
|------|-------------|
| `build_citation_network` | Build citation graph from seed DOIs (depth 1-2, max 200 nodes) |
| `find_co_citation_clusters` | Find papers frequently cited together — reveals thematic communities |
| `get_keyword_trends` | Track keyword frequency in publications over years |

### 🛡️ Citation Verification (3 tools)

| Tool | Description |
|------|-------------|
| `verify_citation` | Verify a single citation exists (author + year + title fragment) |
| `check_retraction` | Check if a paper has been retracted via CrossRef |
| `batch_verify_references` | Verify up to 30 references in one call |

### 📥 Full-Text Pipeline (3 tools)

| Tool | Description |
|------|-------------|
| `check_open_access` | Check OA availability via Unpaywall — returns PDF URL if available |
| `download_pdf` | Download PDF from URL to local path |
| `extract_pdf_text` | Extract text as markdown (marker-pdf ML) or plain text (PyMuPDF fallback) |

### 🧠 Semantic Scholar (3 tools)

| Tool | Description |
|------|-------------|
| `search_semantic_scholar` | Search with TLDR summaries + influential citation counts |
| `get_influential_citations` | Get citations that *substantially engage* with a paper (not incidental) |
| `get_citation_context` | Get exact text snippets where paper A cites paper B |

### 🔄 Rankings Management (1 tool)

| Tool | Description |
|------|-------------|
| `update_rankings` | Download SJR / copy Qualis files. Restart server after updating. |

---

## 📋 MCP Prompts

Pre-built research workflow prompts that any agent can request:

| Prompt | Use Case |
|--------|----------|
| **`research_pipeline`** | Full 7-phase academic research workflow: calibration → discovery → search → curation → full-text → review → verification |
| **`journal_calibrator`** | Build a "Journal DNA" profile — analyze a target journal's methods, theories, writing style, and citation patterns to calibrate your paper |
| **`citation_verification`** | Anti-hallucination protocol — step-by-step guide to verify every reference before submission |
| **`literature_search`** | Systematic search protocol with parallel multi-source queries, deduplication, and snowballing |

Agents can request prompts to get structured research guidance alongside the tools.

---

## 📚 Journal Rankings

### SJR (SCImago Journal Rank)

The SJR CSV contains **32,000+ journals** with quartile classifications, h-index, country, and subject area.

**Option 1 — Manual download (reliable):**
1. Visit [scimagojr.com/journalrank.php](https://www.scimagojr.com/journalrank.php)
2. Click "Download data" (CSV, ~11MB)
3. Save as `data/sjr_rankings.csv`

**Option 2 — Via tool:**
```
Agent: use update_rankings tool
→ Attempts auto-download from scimagojr.com
→ Falls back to manual instructions if blocked
```

**Updating:** When new SJR rankings are released (annually), repeat the download. The `update_rankings` tool handles this.

### Qualis CAPES

Qualis classifications (**170,000+ entries**) for Brazilian academic assessment. No public download URL — requires manual download.

1. Visit [Plataforma Sucupira](https://sucupira.capes.gov.br)
2. Download the Qualis classification spreadsheet (XLSX)
3. Save as `data/qualis_capes.xlsx`, or use:
   ```
   Agent: update_rankings(qualis_path="/path/to/downloaded/file.xlsx")
   ```

> **Note:** The server works perfectly without ranking files. Tools that depend on rankings will return `"N/A"` for missing data, and all other tools function normally.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    MCP Client                                │
│         Claude Desktop / Code / Cursor / OpenClaw            │
├─────────────────────────────────────────────────────────────┤
│                  BX-Scholar MCP Server                       │
│                                                              │
│  Tools (25)              │  Prompts (4)                      │
│  ├── Literature Search   │  ├── research_pipeline            │
│  ├── Paper Metadata      │  ├── journal_calibrator           │
│  ├── Journal Rankings    │  ├── citation_verification        │
│  ├── Bibliometrics       │  └── literature_search            │
│  ├── Citation Verify     │                                   │
│  ├── Full-Text Pipeline  │  Data (local)                     │
│  ├── SciELO              │  ├── SJR CSV (32K journals)       │
│  ├── Semantic Scholar    │  └── Qualis XLSX (170K entries)   │
│  └── Rankings Mgmt       │                                   │
├─────────────────────────────────────────────────────────────┤
│                     External APIs                            │
│  OpenAlex │ CrossRef │ ArXiv │ SciELO │ Semantic Scholar     │
│  Unpaywall │ Tavily                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 📖 Usage Examples

### Find the best journal for your research

```
You: "I study AI adoption in public administration. What are the best journals?"

Agent calls: get_top_journals_for_field("public administration")
Agent calls: search_openalex("artificial intelligence public administration", sort="cited_by_count:desc")
→ Suggests journals ranked by SJR + shows where similar papers were published
```

### Verify references before submission

```
You: "Verify all references in my paper"

Agent calls: batch_verify_references([
  {"author": "Simon", "year": 1955, "title": "behavioral model rational choice"},
  {"author": "DiMaggio", "year": 1983, "title": "iron cage revisited"},
  ...
])
→ ✅ 23/25 verified | ❌ 2 not found — removes unverified citations
```

### Build a citation network

```
You: "Map the citation landscape around these 3 key papers"

Agent calls: build_citation_network("10.1234/paper1,10.5678/paper2,10.9012/paper3", depth=2)
→ Returns nodes + edges for visualization, identifies bridge papers
```

### Check if a paper is Open Access

```
You: "Can I access this paper for free?"

Agent calls: check_open_access("10.1016/j.cities.2023.104567")
→ OA status: "green" | PDF URL: https://repository.example.com/paper.pdf
Agent calls: download_pdf(url, "papers/janssen2023.pdf")
Agent calls: extract_pdf_text("papers/janssen2023.pdf")
→ Full text extracted as structured markdown
```

---

## 🤝 Contributing

Contributions are welcome! Areas where help is needed:

- **New data sources** — DBLP, PubMed, CORE
- **Better PDF extraction** — handling tables, figures, equations
- **Ranking data automation** — reliable auto-download for SJR/Qualis
- **Tests** — unit and integration tests for all tools

```bash
git clone https://github.com/leomcamilo/bx-scholar-mcp.git
cd bx-scholar-mcp
uv sync --extra dev
pytest
```

---

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built by [BaXiJen](https://baxijen.ai)** · Powered by [MCP](https://modelcontextprotocol.io)

*Making AI agents honest about their references, one citation at a time.*

</div>
