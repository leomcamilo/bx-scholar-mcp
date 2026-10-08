# Changelog

All notable changes to `bx-scholar-core` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Optional `OPENALEX_API_KEY` (free). Without a key, OpenAlex now enforces a daily budget shared by everyone on the same IP

### Fixed
- A 429 with a long `Retry-After` (OpenAlex sends hours when the daily budget is gone) raised after ~3 minutes of sleeps and retries; it now fails at once with `QuotaExhaustedError` saying when the quota resets

### Changed
- `search_papers` and OpenAlex search sort by relevance by default (was citation count, which returned the most cited work vaguely matching the terms). Pass `sort="cited_by_count:desc"` to rank by impact. Ported from 1a561db (feat/http-service-and-hardening), which never reached main

### Added
- `pt` source: Portuguese-language works from any venue (OpenAlex `language:pt`), now part of the `br` preset alongside SciELO Brasil
- BDTD, OasisBR and LA Referencia sources (VuFind API): Brazilian and Latin American theses, dissertations and repository articles; `br` and `latam` presets now complete
- `search_theses(query, scope="br"|"latam", degree="all"|"master"|"doctoral", ...)`, with institution, format and advisors per record
- Europe PMC source (`sources="europepmc"`, also in the `bio` preset): PubMed, PMC and preprints, with PMID, PMCID, MeSH and OA flags
- `get_fulltext(identifier, sections, max_chars)`: open-access full text split by section, from Europe PMC JATS XML, without downloading a PDF
- `get_paper` accepts PMID (`pmid:31398324`, PubMed URL) and PMCID (`PMC6789012`, PMC URL)
- `defusedxml` dependency for third-party XML
- `ClientPool`: one instance per API client per server, so per-source rate limits hold across tool calls
- `search_papers` reports failing sources in `errors`, sources missing an API key in `skipped`, and unrecognized names in `unknown_sources`
- `search_papers` presets: `br`, `latam`, `asia`, `bio`, `oa` (sources not implemented yet are ignored)
- `Paper.pmid`, `pmcid`, `mesh`, `language`, `landing_url`, `external_ids`; OpenAlex results now carry PMID, PMCID and MeSH
- Live smoke tests (`pytest -m live`, not run in CI)

### Fixed
- SciELO results no longer claim open access for every record; the status comes from the record
- SciELO search returned nothing: OpenAlex rejects the removed `host_venue` filter (400) and the direct fallback is blocked (403). Now filters SciELO Brasil by DOI prefix 10.1590
- Semantic Scholar search turned errors (including 429) into an empty result
- Tavily results were counted and then dropped; they now come back as `source_type: web`
- `resolve_reference_list` never closed its HTTP clients
- Deduplication also matches records by PMID

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
