# Changelog

All notable changes to `bx-scholar-core` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CORE source (`sources="core"`, also in the `oa` preset): open-access outputs aggregated from repositories of every field
- `get_fulltext` falls back to CORE when Europe PMC has no full text: CORE's extracted text with `CORE_API_KEY` (free), otherwise the PDF URL CORE hosts, ready for `download_pdf`
- `check_open_access` tries CORE when Unpaywall has no PDF; the answer says where the PDF came from (`pdf_source`)
- Optional `CORE_API_KEY` (free). Search works without it; CORE hides `fullText` from requests without a key
- OpenCitations (Index v2 + Meta v1): `get_citations(sources="openalex,opencitations")` (new default) merges both, reporting `total_links` per source and flagging author self-citations; `snowball` and `build_citation_network` accept `citation_sources`
- Optional `OPENCITATIONS_TOKEN` (free)
- Optional `OPENALEX_API_KEY` (free). Without a key, OpenAlex now enforces a daily budget shared by everyone on the same IP

### Fixed
- A 429 with a long `Retry-After` (OpenAlex sends hours when the daily budget is gone) raised after ~3 minutes of sleeps and retries; it now fails at once with `QuotaExhaustedError` saying when the quota resets

### Changed
- **`verify_citation` redesigned** (designed with a Codex review): retrieves candidates from Crossref and OpenAlex together, without a year filter, and decides per candidate. `status` is `verified`, `conflict` (an identified work contradicts the citation), `ambiguous` (several works match) or `insufficient`. A title confirms only as the full title, the main title or a literal contiguous passage of 4+ content words; similarity never confirms; a field missing on the record is not a match; authors use the source's family/given structure and every cited author must match a distinct record author. New `title_mode` (`auto`/`full`/`fragment`) and `next_action`; the "may be fabricated" message is gone. Replaces `citation_match.py`
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
- `verify_citation`, fourth Codex review: two spelled-out institutions are compared by name only (generated acronyms collide: Cambridge/Chicago); capitals count as grouped initials only without vowels ("JD", not "WEI"); every given name present on both sides must agree ("John Stuart" vs "John James"); a capital single letter inside the fragment is a discriminant ("Hepatitis A" vs "B", "HLA-A" vs "HLA-B"); institution names are not split at "and"/"e"; a bare compound surname ("García Márquez") is accepted; spaces never join words to numbers, and spaced/joined spellings ("interleukin 6", "COVID 19", "3-D") are reconciled by matching letter and digit runs in sequence
- `verify_citation`, third Codex review: authors are parsed the same way on both sides (surname vs given names, grouped and hyphenated initials, eastern order only for two-word names) and must share the surname plus a compatible first given name, so "John Jones" no longer matches "John J. Smith"; organization acronyms only match organizations ("WHO" no longer matches "William Henry Oswald"); alphanumeric terms stay whole (H2O vs H2S, C2H6O vs C6H2O), are counted with multiplicity, and single letters and roman numerals are discriminants (Vitamin D vs C, Phase I vs II); an em dash marks a subtitle
- `verify_citation`, second Codex review: numbers and short terms (IL-6, AI, COVID-19, BRCA1) must all appear exactly in the record title, in every comparison route; letters and digits are split and any dash is a separator, so "COVID–19", "COVID 19" and "COVID19" agree; the cited author is matched by the whole name within one record author, accepting grouped initials ("Smith JD"), eastern order ("Wang Wei" ~ "W. Wang"), abbreviated compound surnames and corporate authors or their acronym; a fragment equal to the main title (before a subtitle) matches; DOIs with a subdivided prefix (10.1000.10/123) are accepted
- `verify_citation` (found in a Codex review): a shared given name counted as an author match ("John Jones" verified "John Smith"); short surnames like "Li" were skipped; different CJK titles normalized to empty strings and matched perfectly; short and numbered terms were dropped, so "IL-6" verified "IL-8" and "AI" verified "AR". The cited surname is now matched within one record author, letters of any script are kept, and short or numbered tokens must match exactly
- Europe PMC `lookup` validates the DOI before quoting it (a crafted DOI could add OR clauses and return another paper) and only returns a hit carrying the requested identifier
- `get_fulltext`: `max_chars` also caps the first section (`truncated_section`), and HTTP errors other than 404 are reported as errors instead of "no full text"
- Deduplication merges records sharing any identifier, including a copy with DOI+PMID and one with PMID only
- `load_settings(project_root=...)` reads the `.env` of that root
- `get_citations` and `snowball` claimed to accept OpenAlex IDs but built a DOI URL from them and returned nothing; OpenAlex IDs, PMIDs and arXiv IDs are now resolved to a DOI first
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
