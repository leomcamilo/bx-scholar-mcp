# Systematic Literature Search: {topic}

## Principles
1. **Recall > Precision in search, Precision > Recall in curation.** Search broadly, filter later.
2. **Each API has different strengths.** OpenAlex = coverage + abstracts. CrossRef = DOIs + precise metadata. ArXiv = CS/AI preprints. SciELO = Brazilian/LATAM OA. Semantic Scholar = TLDR + influential citations.
3. **Iteration is expected.** The first query is rarely perfect. Evaluate sample, refine, repeat.

## Step 1: Term expansion
For each concept in the research, generate synonyms and related terms:
```
Concept: {topic}
  EN: [synonyms, related terms, broader/narrower terms]
  PT: [Portuguese equivalents for SciELO/SPELL searches]
  Truncation: [wildcards for variant forms]
```
Generate 3-5 alternative queries.

## Step 2: Execute parallel searches
For each query:
1. search_openalex(query, year_from=2019, per_page=50, sort="cited_by_count:desc") — primary, 250M+ papers
2. search_crossref(query, year_from=2019, rows=50) — DOI verification, precise metadata
3. search_scielo(query, year_from=2019) — essential for Brazilian/LATAM topics, 100% Open Access
4. search_semantic_scholar(query, year="2019-", limit=50) — TLDR summaries + influential citation counts
5. search_arxiv(query, max_results=20) — CS/AI preprints, ALL results are GREY LITERATURE
6. get_journal_papers(target_issn, query, year_from=2019) — MANDATORY for target journal

## Step 3: Deduplicate
- By DOI: exact match across all sources
- For papers without DOI: title similarity >90% + same year + same first author
- Keep the version with the most complete metadata

## Step 4: Snowball from key papers
For the top 5-10 most-cited papers:
- get_paper_citations(doi, direction="citing", per_page=10) — forward snowballing
- get_paper_citations(doi, direction="references", per_page=10) — backward snowballing

## Step 5: Quality filter
For each paper:
- lookup_journal_ranking(issn) — classify by SJR + Qualis + JQL tier
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