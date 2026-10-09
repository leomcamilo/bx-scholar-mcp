# BX-Scholar Core

MCP server for academic research. 23 tools over 14 free APIs, with journal rankings, citation verification and a DuckDB cache.

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

## Tools (23)

| Category | Tool | Description |
|----------|------|-------------|
| **Search** | `search_papers` | Search one or more sources (see below), deduplicated, with per-source counts and errors |
| | `search_theses` | Master's and doctoral theses: BDTD (`scope="br"`) or BDTD + LA Referencia (`scope="latam"`) |
| | `search_journal_papers` | Search within a journal by ISSN |
| **Get** | `get_paper` | Paper by DOI, arXiv ID, OpenAlex ID, PMID or PMCID |
| | `get_author` | Author profile + works by name |
| | `get_journal_info` | Journal metadata from OpenAlex, with rankings |
| | `get_citations` | Citing papers or references, merged from OpenAlex and OpenCitations |
| | `get_keyword_trends` | Keyword frequency over time |
| | `snowball` | Iterative backward/forward snowballing from seed papers (Wohlin) |
| | `resolve_reference_list` | Raw bibliography text to verified papers with DOIs and abstracts |
| **Rankings** | `rank_journal` | SJR + Qualis CAPES + JQL lookup with fuzzy matching |
| | `top_journals_for_field` | Top-ranked journals for a research field |
| **Verification** | `verify_citation` | Confirms a citation only when exactly one work matches title, every cited author and year (±1). Returns `verified`, `conflict`, `ambiguous` or `insufficient`, with the reason and the next step |
| | `check_retraction` | Check if a paper has been retracted |
| | `batch_verify_references` | Same rules as `verify_citation`, up to 30 references |
| **Citations** | `get_influential_citations` | Citations that substantially engage with a paper (Semantic Scholar) |
| | `get_citation_context` | Exact text snippets of how a paper is cited |
| | `build_citation_network` | Citation graph from seed DOIs |
| | `find_co_citation_clusters` | Papers frequently cited together |
| **Full text** | `get_fulltext` | Open-access text split by section, from Europe PMC or CORE, without a PDF |
| | `check_open_access` | OA status and PDF URL: Unpaywall, then CORE |
| | `download_pdf` | Download a PDF from a public URL into the cache |
| | `extract_pdf_text` | Extract text from a cached PDF (marker-pdf or pymupdf) |

## Sources

`search_papers(sources=...)` takes source names and presets, comma-separated. The default is `openalex,crossref`.

| Source | Covers |
|--------|--------|
| `openalex` | 250M+ works, abstracts, PMID and MeSH |
| `crossref` | DOI metadata |
| `semantic_scholar` | TLDRs, influential citation counts |
| `arxiv` | Preprints, marked as grey literature |
| `europepmc` | PubMed, PMC and preprints, with MeSH and OA flags |
| `core` | Open-access outputs from repositories worldwide |
| `scielo` | SciELO Brasil (DOI prefix 10.1590, through OpenAlex) |
| `pt` | Portuguese-language works from any venue (OpenAlex) |
| `bdtd` | Brazilian theses and dissertations (IBICT) |
| `oasisbr` | Brazilian repositories and journals (IBICT) |
| `lareferencia` | Latin American repositories |
| `cinii` | Japanese articles (CiNii Research, NII) |
| `jstage` | Articles of Japanese scholarly societies (JST), in the article's language |
| `tavily` | Web search, for reports and policy documents (needs `TAVILY_API_KEY`) |

| Preset | Sources |
|--------|---------|
| `br` | scielo, pt, bdtd, oasisbr |
| `latam` | scielo, lareferencia, oasisbr |
| `asia` | cinii, jstage |
| `bio` | europepmc, openalex |
| `oa` | core, openalex |

The response counts results per source in `per_source`. A source that fails is listed in `errors` while the others still answer; a source missing its key is listed in `skipped`.

## Configuration

| Variable | Required | Description |
|----------|:--------:|-------------|
| `POLITE_EMAIL` | **Yes** | Email for polite API pools (OpenAlex, CrossRef, Unpaywall) |
| `TAVILY_API_KEY` | No | Tavily web search API key |
| `S2_API_KEY` | No | Semantic Scholar API key (5 req/s vs 1 req/s) |
| `OPENALEX_API_KEY` | No | OpenAlex API key (free). Without it, requests count against a daily budget shared per IP |
| `OPENCITATIONS_TOKEN` | No | OpenCitations token (free). Raises the rate limit |
| `CORE_API_KEY` | No | CORE API key (free). Search works without it; `get_fulltext` reads CORE's extracted text only with a key |
| `CINII_APPID` | No | CiNii Research application ID (free). Search works without it |
| `BX_SCHOLAR_HOME` | No | Directory holding `.env` and `data/` (default: repo root, found by walking up from cwd) |
| `BX_SCHOLAR_DATA_DIR` | No | Directory for ranking data files, relative to `BX_SCHOLAR_HOME` (default: `data/`) |
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
| `fulltext` | 30 days | `get_fulltext` (Europe PMC) |
| `keyword_trends` | 24 hours | `get_keyword_trends` |

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
