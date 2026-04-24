# Changelog

All notable changes to `bx-scholar-core` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-24

### Added

#### Tools (19)
- **Search**: `search_papers` (unified multi-source with dedup), `search_journal_papers`
- **Get**: `get_paper` (smart ID resolution), `get_author`, `get_journal_info`, `get_citations`, `get_keyword_trends`
- **Rankings**: `rank_journal` (fuzzy match), `top_journals_for_field`
- **Verification**: `verify_citation`, `check_retraction`, `batch_verify_references`
- **Citations**: `get_influential_citations`, `get_citation_context`, `build_citation_network`, `find_co_citation_clusters`
- **Full-text**: `check_open_access`, `download_pdf`, `extract_pdf_text`

#### API Clients (7)
- OpenAlex (10 req/s), CrossRef (50 req/s), Semantic Scholar (1-5 req/s), ArXiv (1/3s), SciELO (5 req/s), Unpaywall (10 req/s), Tavily (5 req/s)
- Per-host rate limiting via aiolimiter
- Retry with exponential backoff + jitter for 429/5xx (tenacity)
- Retry-After header respected with capped sleep

#### Cache
- DuckDB-backed persistent cache with per-entity TTLs
- Entity types: search_results (1h), paper_metadata (7d), citations (24h), author (7d), journal_info (30d), verification (24h), oa_status (7d), keyword_trends (24h), web_search (1h)
- Transparent integration at HTTP client level via `cache_policy` parameter
- In-memory mode for tests, file-based for production
- Cache stats, eviction, and clear operations

#### Models
- Canonical `Paper`, `Author`, `Venue` models with DOI/ISSN normalization
- `JournalMetrics` with `best_tier` across SJR/Qualis/JQL ranking systems
- `VerificationResult`, `RetractionStatus` for citation verification

#### Rankings
- SJR (32K+ journals, CSV), Qualis CAPES (170K+ entries, XLSX), Harzing's JQL (CSV)
- Fuzzy journal name matching via rapidfuzz (>85% threshold)

#### Infrastructure
- Monorepo with uv workspaces (`packages/bx-scholar-core`, `packages/bx-scholar-workflow`)
- pydantic-settings configuration with email validation
- structlog logging (console/JSON)
- Smart ID resolution (DOI, arXiv, OpenAlex, Semantic Scholar)
- Paper deduplication (DOI exact + title similarity >90%)
- Public API exports via `__init__.py`
- PEP 561 `py.typed` marker
- Shared test fixtures via `conftest.py`
- GitHub Actions CI (lint + test)
- 150 unit tests
