# BX-Scholar MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Academic research toolkit as an MCP server. 25+ tools for literature search, citation verification, journal rankings, bibliometrics, and PDF extraction — all accessible from Claude Desktop, Claude Code, Cursor, or any MCP-compatible client.

## Features

| Group | Tools | Description |
|-------|-------|-------------|
| **Literature Search** | `search_openalex`, `search_crossref`, `search_arxiv`, `search_tavily` | Multi-source academic search with deduplication support |
| **Paper Metadata** | `get_paper_by_doi`, `get_paper_citations`, `get_author_works`, `get_journal_info` | Full metadata retrieval, citation graphs, author profiles |
| **Journal Rankings** | `lookup_journal_ranking`, `get_top_journals_for_field`, `get_journal_papers` | Local SJR + Qualis CAPES lookup (no API call), field-level rankings |
| **Bibliometrics** | `build_citation_network`, `find_co_citation_clusters`, `get_keyword_trends` | Citation network mapping, co-citation analysis, trend tracking |
| **Citation Verification** | `verify_citation`, `check_retraction`, `batch_verify_references` | Anti-hallucination: verify every citation exists before submission |
| **Full-Text Pipeline** | `check_open_access`, `download_pdf`, `extract_pdf_text` | Unpaywall OA check, PDF download, text extraction (marker-pdf + pymupdf) |
| **SciELO** | `search_scielo` | Brazilian/Latin American Open Access papers |
| **Semantic Scholar** | `search_semantic_scholar`, `get_influential_citations`, `get_citation_context` | TLDR summaries, influential citations, citation context snippets |
| **Rankings Management** | `update_rankings` | Download/update SJR and Qualis data files |

## Quick Start

### Using uvx (recommended)

```bash
uvx --from git+https://github.com/leomcamilo/bx-scholar-mcp.git mcp run run_server.py
```

### Using uv

```bash
git clone https://github.com/leomcamilo/bx-scholar-mcp.git
cd bx-scholar-mcp
uv run run_server.py
```

### Using pip

```bash
git clone https://github.com/leomcamilo/bx-scholar-mcp.git
cd bx-scholar-mcp
pip install -e .
python run_server.py
```

## Configuration

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

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

### Cursor

Add to `.cursor/mcp.json`:

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

## Journal Rankings

### SJR (SCImago Journal Rank)

The server will warn on startup if the SJR file is missing. To get rankings:

1. **Manual download (recommended)**: Go to [scimagojr.com/journalrank.php](https://www.scimagojr.com/journalrank.php), click "Download data", and save the CSV to `data/sjr_rankings.csv`
2. **Via tool**: After the server starts, use the `update_rankings()` tool to attempt auto-download (scimagojr.com may block automated requests)

### Qualis CAPES

Qualis classifications are not available via public URL. To use:

1. Download from [Plataforma Sucupira](https://sucupira.capes.gov.br) (requires login)
2. Save as `data/qualis_capes.xlsx`, or use the `update_rankings(qualis_path="/path/to/file.xlsx")` tool

The server works without rankings files — ranking-dependent tools will return "N/A" for missing data.

## MCP Prompts

The server includes 4 workflow prompts accessible via `use_mcp_tool` or the prompts menu:

| Prompt | Description |
|--------|-------------|
| `research_pipeline` | Complete 7-phase research workflow from topic discovery to journal submission |
| `journal_calibrator` | Build a "Journal DNA" profile to calibrate your paper to a target venue |
| `citation_verification` | Anti-hallucination protocol — verify every citation before submission |
| `literature_search` | Systematic search protocol with parallel multi-source queries and snowballing |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POLITE_EMAIL` | Yes | Your email for polite API pools (OpenAlex, CrossRef, Unpaywall) |
| `TAVILY_API_KEY` | No | Tavily web search API key |
| `S2_API_KEY` | No | Semantic Scholar API key ([get free key](https://www.semanticscholar.org/product/api#api-key-form)) |

Create a `.env` file from the template:

```bash
cp .env.example .env
```

## API Reference

### Literature Search
- **search_openalex** — Search OpenAlex with filters (year, journal, type, sort)
- **search_crossref** — Search CrossRef for DOI verification and metadata
- **search_arxiv** — Search ArXiv preprints (marked as grey literature)
- **search_tavily** — Web search for academic content and policy documents

### Paper Metadata
- **get_paper_by_doi** — Full metadata by DOI (OpenAlex + CrossRef fallback)
- **get_paper_citations** — Forward/backward citation snowballing
- **get_author_works** — Author profile and publications sorted by citations
- **get_journal_info** — Journal metadata with SJR/Qualis rankings

### Journal Rankings
- **lookup_journal_ranking** — Fast local SJR + Qualis lookup by ISSN or name
- **get_top_journals_for_field** — Top journals in a research field by SJR
- **get_journal_papers** — Search papers within a specific journal

### Bibliometrics
- **build_citation_network** — Citation network from seed DOIs (1-2 levels)
- **find_co_citation_clusters** — Co-citation pairs analysis
- **get_keyword_trends** — Keyword frequency trends over time

### Citation Verification
- **verify_citation** — Verify a single citation exists (anti-hallucination)
- **check_retraction** — Check if a paper has been retracted
- **batch_verify_references** — Verify up to 30 references in one call

### Full-Text Pipeline
- **check_open_access** — Check OA status via Unpaywall
- **download_pdf** — Download PDF from URL to local path
- **extract_pdf_text** — Extract text as markdown or plain text (marker-pdf / pymupdf)

### SciELO
- **search_scielo** — Brazilian/LATAM Open Access papers

### Semantic Scholar
- **search_semantic_scholar** — Search with TLDR summaries and influential citation counts
- **get_influential_citations** — Citations that substantially engage with a paper
- **get_citation_context** — Exact text snippets where one paper cites another

### Rankings Management
- **update_rankings** — Download/update SJR and Qualis ranking files

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/new-tool`)
3. Add tests for new tools
4. Submit a pull request

## License

MIT License. See [LICENSE](LICENSE) for details.
