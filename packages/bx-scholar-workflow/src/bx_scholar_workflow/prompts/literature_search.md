# Systematic Literature Search: {topic}

## Principles
1. **Recall > Precision in search, Precision > Recall in curation.** Search broadly, filter later.
2. **Each source covers different ground.** OpenAlex has the widest coverage and abstracts. CrossRef has the most precise DOI metadata. ArXiv holds CS/AI preprints. Semantic Scholar adds TLDRs and influential citation counts. Europe PMC covers PubMed and PMC. CORE holds open-access copies from repositories. Brazil, Latin America and Japan have their own sources and presets (Step 2).
3. **Iteration is expected.** The first query is rarely perfect. Evaluate sample, refine, repeat.

## Step 1: Term expansion
For each concept in the research, generate synonyms and related terms:
```
Concept: {topic}
  EN: [synonyms, related terms, broader/narrower terms]
  PT: [Portuguese equivalents for the br/latam sources and SPELL]
  JA: [Japanese equivalents when the topic has Japanese literature, for cinii/jstage]
  Truncation: [wildcards for variant forms]
```
Generate 3-5 alternative queries.

## Step 2: Execute parallel searches
For each query, one `search_papers` call per group of sources (results come deduplicated, with counts in `per_source` and failed sources in `errors`):
1. search_papers(query, sources="openalex,crossref,semantic_scholar", year_from=2019, per_page=50) — primary. Sorted by relevance; add sort="cited_by_count:desc" to rank by impact
2. search_papers(PT query, sources="br", year_from=2019) — Brazil: SciELO Brasil, Portuguese-language works, BDTD, OasisBR. Use sources="latam" for Latin America (SciELO, LA Referencia, OasisBR)
3. search_papers(query, sources="bio") — health and life sciences: Europe PMC + OpenAlex
4. search_papers(query, sources="asia") — Japan: CiNii Research + J-STAGE (run it with the JA terms too)
5. search_papers(query, sources="oa") — open-access copies in repositories (CORE + OpenAlex)
6. search_papers(query, sources="arxiv", per_page=20) — CS/AI preprints, ALL results are GREY LITERATURE
7. search_journal_papers(target_issn, query, year_from=2019) — MANDATORY for target journal
8. search_theses(PT query, scope="br" or "latam") — master's and doctoral theses, which OpenAlex barely covers for Latin America

Run only the groups that fit the topic. A source listed in `errors` failed: retry it, never read its silence as "no papers".

## Step 3: Deduplicate
- By DOI: exact match across all sources
- For papers without DOI: title similarity >90% + same year + same first author
- Keep the version with the most complete metadata

## Step 4: Snowball from key papers
For the top 5-10 most-cited papers:
- snowball(seed_identifiers, direction="both") — iterative forward and backward snowballing (Wohlin)
- or, one paper at a time: get_citations(doi, direction="citing" | "references", per_page=10). It merges OpenAlex and OpenCitations by default

## Step 5: Quality filter
For each paper:
- rank_journal(issn) — classify by SJR + Qualis + JQL tier
- Tier S/A (Q1-Q2 / A1-A2): always include
- Tier B (Q2-Q3 / A3-A4): include if relevant
- Tier C+ (Q3+ / B1+): only if essential (seminal works)
- ArXiv/preprints: supplementary only, NEVER primary source for journal publications

## Step 6: Register all searches
Document each search:
| API | Query | Filters | Results | Date |
|-----|-------|---------|---------|------|
| OpenAlex | "..." | year>2019 | N | {topic} |
| CrossRef | "..." | year>2019 | N | ... |

## Output
Deliver to curation phase:
- Complete list with: title, authors, year, DOI, journal, ISSN, source_type, tier
- Total found per API, duplicates removed
- Log of all queries executed