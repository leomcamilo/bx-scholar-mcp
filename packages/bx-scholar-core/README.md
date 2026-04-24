# BX-Scholar Core

MCP server for academic research — 19 tools across 7 API sources with DuckDB caching, journal rankings, and citation verification.

Part of the [bx-scholar-mcp](https://github.com/leomcamilo/bx-scholar-mcp) monorepo.

## Quick Start

```bash
# Set your email (required for polite API pools)
export POLITE_EMAIL="you@university.edu"

# Run as MCP server
uvx --from "git+https://github.com/leomcamilo/bx-scholar-mcp#subdirectory=packages/bx-scholar-core" bx-scholar-core
```

### MCP Client Configuration

```json
{
  "mcpServers": {
    "bx-scholar-core": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/leomcamilo/bx-scholar-mcp#subdirectory=packages/bx-scholar-core", "bx-scholar-core"],
      "env": { "POLITE_EMAIL": "you@university.edu" }
    }
  }
}
```

## Tools (19)

| Category | Tool | Description |
|----------|------|-------------|
| **Search** | `search_papers` | Multi-source search (OpenAlex, CrossRef, ArXiv, SciELO, S2, Tavily) with dedup |
| | `search_journal_papers` | Search within a journal by ISSN |
| **Get** | `get_paper` | Paper by DOI, arXiv ID, or OpenAlex ID (smart resolution) |
| | `get_author` | Author profile + works by name |
| | `get_journal_info` | Journal metadata from OpenAlex |
| | `get_citations` | Citing papers or references for a DOI |
| | `get_keyword_trends` | Keyword frequency over time |
| **Rankings** | `rank_journal` | SJR + Qualis CAPES + JQL lookup with fuzzy matching |
| | `top_journals_for_field` | Top-ranked journals for a research field |
| **Verification** | `verify_citation` | Anti-hallucination: verify citation exists |
| | `check_retraction` | Check if paper has been retracted |
| | `batch_verify_references` | Batch verify up to 30 references |
| **Citations** | `get_influential_citations` | Citations that substantially engage with a paper |
| | `get_citation_context` | Exact text snippets of how a paper is cited |
| | `build_citation_network` | Build citation graph from seed DOIs |
| | `find_co_citation_clusters` | Papers frequently cited together |
| **Full-text** | `check_open_access` | OA status via Unpaywall |
| | `download_pdf` | Download PDF from URL |
| | `extract_pdf_text` | Extract text (marker-pdf or pymupdf) |

## Configuration

| Variable | Required | Description |
|----------|:--------:|-------------|
| `POLITE_EMAIL` | **Yes** | Email for polite API pools (OpenAlex, CrossRef, Unpaywall) |
| `TAVILY_API_KEY` | No | Tavily web search API key |
| `S2_API_KEY` | No | Semantic Scholar API key (5 req/s vs 1 req/s) |
| `BX_SCHOLAR_DATA_DIR` | No | Directory for ranking data files (default: `data/`) |
| `BX_SCHOLAR_CACHE_ENABLED` | No | Enable DuckDB cache (default: `true`) |
| `BX_SCHOLAR_CACHE_DIR` | No | Cache directory (default: `~/.cache/bx-scholar/`) |

## Cache

Responses are cached in DuckDB with per-entity TTLs:

| Entity | TTL | Examples |
|--------|-----|----------|
| `search_results` | 1 hour | `search_papers`, `search_journal_papers` |
| `paper_metadata` | 7 days | `get_paper` |
| `citations` | 24 hours | `get_citations`, `get_influential_citations` |
| `author` | 7 days | `get_author` |
| `journal_info` | 30 days | `get_journal_info` |
| `verification` | 24 hours | `verify_citation`, `check_retraction` |
| `oa_status` | 7 days | `check_open_access` |

Cache is stored at `~/.cache/bx-scholar/bx_scholar_cache.duckdb`. Disable with `BX_SCHOLAR_CACHE_ENABLED=false`.

## Use as Library

```python
from bx_scholar_core import Paper, Author, CacheStore, Settings, create_server
```

## Development

```bash
cd packages/bx-scholar-core
uv sync --extra dev
uv run pytest -x -q        # run tests
uv run ruff check .         # lint
uv run ruff format --check  # format check
```

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for full guidelines.

## License

MIT
