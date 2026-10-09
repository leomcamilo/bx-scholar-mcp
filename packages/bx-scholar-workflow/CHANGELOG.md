# Changelog

All notable changes to `bx-scholar-workflow` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Prompts and skills told agents to call tools that no longer exist (`search_openalex`, `search_crossref`, `search_scielo`, `get_paper_by_doi`, `get_paper_citations`, `lookup_journal_ranking`, `get_journal_papers` and five more), so an agent following them failed on the first call. They now use the current names, and a test fails if a prompt or skill names a tool the server does not register
- `citation_verification` asked for "key words from title", which `verify_citation` no longer accepts as proof; it now asks for the full title (or a literal passage of 4+ content words) and explains each status

### Changed
- Search steps use `search_papers(sources=...)` with the presets `br`, `latam`, `asia`, `bio` and `oa`, plus `search_theses` for theses and `snowball` for snowballing
- Reading steps try `get_fulltext` before the PDF route

## [0.1.0] - 2026-04-24

### Added

#### Prompts (8)
- `research_pipeline` — 13-phase orchestrator from topic to submission
- `journal_calibrator(journal_name)` — Journal DNA profiling and strategic positioning
- `citation_verification` — Anti-hallucination gate with batch verification
- `literature_search(topic)` — Systematic multi-source search protocol
- `revise_and_resubmit` — R&R protocol with response letter generation
- `qualitative_analysis` — Method selection (Gioia, Braun & Clarke, content analysis, process tracing)
- `theory_development` — Theory building, extension, integration guide
- `meta_analysis_protocol` — Meta-analysis workflow with AMSTAR 2

#### Skills (21 resources)
- `research-pipeline` — Full orchestrator with UX protocol and backtracking
- `journal-calibrator` — Editor persona and strategic positioning
- `discovery` — Research topic discovery and validation
- `systematic-search` — Autonomous multi-source search execution
- `curation` — Paper quality curation with real rankings
- `paper-reader` — Structured reading notes (Obsidian-compatible)
- `literature-review` — Argumentative literature review writing
- `methodology` — Research design with analysis_spec output
- `results-discussion` — Results and discussion section writing
- `academic-writing` — CARS Introduction, Conclusion, Abstract
- `internal-review` — Adversarial paper review with desk rejection simulation
- `revise-resubmit` — R&R response management
- `reference-manager` — BibTeX generation, multi-style formatting
- `formatter` — Journal submission formatting
- `submission` — Venue selection and submission package
- `conclusion` — Synthesis, contributions, limitations
- `prisma` — PRISMA 2020 systematic review protocol
- `compliance` — Ethics, IRB/CEP, LGPD, Open Science
- `theory-development` — Theoretical contribution development
- `qualitative-analysis` — Publication-quality qualitative analysis
- `meta-analysis` — Quantitative meta-analysis

#### Infrastructure
- External `.md` content files for prompts and skills
- Prompt/skill loaders using `importlib.resources`
- Server composition: core tools (19) + workflow prompts (8) + skill resources (21)
- 11 unit tests
