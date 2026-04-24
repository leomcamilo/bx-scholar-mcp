# Citation Verification Protocol — Anti-Hallucination Gate

NEVER submit a manuscript without running this protocol. This is a MANDATORY gate before finalizing ANY written section.

## Why This Matters
AI agents hallucinate references. They cite papers that do not exist, fabricate DOIs, and attribute quotes to wrong authors. This protocol ensures zero ghost references.

## Step 1: Compile all references
List every citation in your manuscript: author, year, title fragment, DOI (if available).

## Step 2: Batch verify
```
batch_verify_references([
    {"author": "Author1", "year": 2020, "title": "key words from title"},
    {"author": "Author2", "year": 2019, "title": "key words from title"},
    ...
])
```
This checks up to 30 references against CrossRef + OpenAlex in one call.

## Step 3: Handle unverified references
For each unverified reference:
1. Try verify_citation(author, year, title) with alternative title fragments or spelling variants
2. Try get_paper_by_doi(doi) if you have the DOI
3. Search with search_openalex or search_crossref using author + key terms
4. If STILL unverified after all attempts: **REMOVE THE CITATION ENTIRELY**. Do not guess. Do not keep it "just in case."

## Step 4: Check retractions
For EVERY verified reference with a DOI:
```
check_retraction(doi)
```
- If retracted: REMOVE immediately. No exceptions, no footnotes.
- If expression of concern: FLAG prominently and discuss with researcher.

## Step 5: Enrich metadata
For verified references missing metadata:
- get_paper_by_doi(doi) — complete metadata (volume, issue, pages, publisher)
- lookup_journal_ranking(issn) — verify journal quality (SJR, Qualis, JQL)

## Step 6: Quality audit
- Verify that reference list meets target journal standards:
  - Sufficient Tier A-B sources (70%+ for journal publications)
  - Minimum 3-5 papers from the target journal itself
  - Appropriate recency (% from last 5 years matches journal norms)
  - No predatory journals (check against SJR/Qualis/JQL)

## Step 7: Final report
Produce verification report:
| # | Reference | Status | DOI | Notes |
|---|-----------|--------|-----|-------|
| 1 | Author (Year) | VERIFIED | 10.xxx | — |
| 2 | Author (Year) | VERIFIED + RETRACTED | 10.xxx | Retracted 2021-03 |
| 3 | Author (Year) | UNVERIFIED | — | REMOVED |

**Rule: If removing a citation leaves a claim unsupported, either find a verified replacement or remove the claim.**