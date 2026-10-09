# Citation Verification Protocol — Anti-Hallucination Gate

NEVER submit a manuscript without running this protocol. This is a MANDATORY gate before finalizing ANY written section.

## Why This Matters
AI agents hallucinate references. They cite papers that do not exist, fabricate DOIs, and attribute quotes to wrong authors. This protocol ensures zero ghost references.

## Step 1: Compile all references
List every citation in your manuscript: authors, year, FULL title as cited, DOI (if available).

## Step 2: Batch verify
```
batch_verify_references('[
    {"author": "Mergel, I.; Edelmann, N.", "year": 2019, "title": "Defining digital transformation: Results from expert interviews", "title_mode": "full"},
    {"author": "Silva et al.", "year": 2020, "title": "the full title as it appears in the reference list"},
    ...
]')
```
The argument is a JSON string. One call checks up to 30 references against CrossRef + OpenAlex.

Give the full title, the main title (before the subtitle) or a literal passage of at least 4 content words, in the original word order. Loose key words do not confirm anything. Use title_mode="full" when you are giving the complete title: a different title then counts as a conflict.

Each reference comes back with a status:
- `verified`: exactly one work matches title, every cited author and the year (±1 lowers confidence to medium). Keep it, with the DOI returned.
- `conflict`: the work was identified and contradicts the citation (other authors, year off by 2+, different full title). Fix the citation from the record in closest_match, or remove it.
- `ambiguous`: several works match. Add the DOI, the full title or the year to tell them apart.
- `insufficient`: not enough to decide (partial title, record without authors or year, nothing found). Follow next_action. Not finding a work does not prove it was made up, and finding a similar one does not prove it exists.

## Step 3: Handle references that are not verified
For each reference that is not verified:
1. Read rejected_because and next_action, then try verify_citation(author, year, title_fragment, title_mode) again with the full title or the missing field
2. Try get_paper(doi) if you have the DOI
3. Search with search_papers(author + title words) (OpenAlex + CrossRef by default) to find the real record
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
- get_paper(doi) — complete metadata (volume, issue, pages, publisher)
- rank_journal(issn) — verify journal quality (SJR, Qualis, JQL)

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