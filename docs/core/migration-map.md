# Migration Map: Monolith → bx-scholar-core

Maps every tool from the legacy `run_server.py` to its equivalent in `bx-scholar-core`.

## Tool Mapping

| # | Legacy Tool | Core Tool | Notes |
|---|-------------|-----------|-------|
| 1 | `search_openalex` | `search_papers(sources="openalex")` | Unified search with source selection |
| 2 | `search_crossref` | `search_papers(sources="crossref")` | Same |
| 3 | `search_arxiv` | `search_papers(sources="arxiv")` | Same |
| 4 | `search_tavily` | `search_papers(sources="tavily")` | Same |
| 5 | `search_scielo` | `search_papers(sources="scielo")` | Same |
| 6 | `search_semantic_scholar` | `search_papers(sources="semantic_scholar")` | Same |
| 7 | `get_paper_by_doi` | `get_paper` | Now accepts DOI, arXiv, OpenAlex, S2 IDs |
| 8 | `get_paper_citations` | `get_citations` | Renamed, same functionality |
| 9 | `get_author_works` | `get_author` | Renamed, same functionality |
| 10 | `get_journal_info` | `get_journal_info` | Same name, same functionality |
| 11 | `lookup_journal_ranking` | `rank_journal` | Renamed, now includes fuzzy match |
| 12 | `get_top_journals_for_field` | `top_journals_for_field` | Renamed |
| 13 | `get_journal_papers` | `search_journal_papers` | Renamed for clarity |
| 14 | `build_citation_network` | `build_citation_network` | Same |
| 15 | `find_co_citation_clusters` | `find_co_citation_clusters` | Same |
| 16 | `get_keyword_trends` | `get_keyword_trends` | Same |
| 17 | `verify_citation` | `verify_citation` | Same |
| 18 | `check_retraction` | `check_retraction` | Same |
| 19 | `batch_verify_references` | `batch_verify_references` | Same |
| 20 | `check_open_access` | `check_open_access` | Same |
| 21 | `download_pdf` | `download_pdf` | Same |
| 22 | `extract_pdf_text` | `extract_pdf_text` | Same |
| 23 | `get_influential_citations` | `get_influential_citations` | Same |
| 24 | `get_citation_context` | `get_citation_context` | Same |
| 25 | `update_rankings` | *(deferred to Phase 5)* | Will be a CLI script, not a tool |

## Key Changes

### Consolidated: 6 search tools → 1 unified `search_papers`

The legacy server had separate tools for each source. The core consolidates them into `search_papers(query, sources="openalex,crossref")` with automatic deduplication.

Users can still query a single source: `search_papers(query, sources="openalex")`.

### Smart ID Resolution

`get_paper` now accepts any identifier format:
- DOI: `10.1234/test` or `https://doi.org/10.1234/test`
- ArXiv: `2401.12345` or `arXiv:2401.12345v2`
- OpenAlex: `W12345`
- Semantic Scholar: 40-char hex ID

### Fuzzy Journal Lookup

`rank_journal` (formerly `lookup_journal_ranking`) now supports fuzzy name matching via `rapidfuzz` (>85% threshold).

### Deferred to Phase 5

- `update_rankings` → `scripts/fetch_rankings.py` CLI script

## Prompts and Resources

All 8 prompts and 21 resources have been moved to `bx-scholar-workflow`. They are not part of `bx-scholar-core`.

See `packages/bx-scholar-workflow/` for the workflow package.
