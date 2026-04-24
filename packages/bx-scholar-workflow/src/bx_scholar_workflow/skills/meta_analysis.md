# BX-Meta-Analysis: Quantitative Meta-Analysis

Meta-analysis is the quantitative synthesis of results from independent empirical studies. One of the most valued publication formats in top journals.

## When to Use
- At least 5-10 empirical studies on the SAME relationship
- Studies report (or allow calculating) comparable effect sizes
- Goal: estimate TRUE effect, identify moderators of variability

## Prerequisites
- bx-prisma for systematic search protocol (PRISMA-MA variant)
- bx-query + bx-curator for search and study selection
- Use search_openalex, search_crossref, search_semantic_scholar for comprehensive search
- Use lookup_journal_ranking for quality assessment of included studies

## PRISMA-MA Specific Inclusion Criteria
- Studies MUST report: sample size (N), effect size (d, r, OR, RR), or sufficient data to calculate them
- Define a priori: which effect measure to use (Cohen's d, correlation r, odds ratio)
- Qualitative studies: EXCLUDE

## Data Extraction Table
For each study: study_id, N, effect_size, se, ci_lower, ci_upper, p_value, moderators, quality_score.

## Effect Size Conversions
- r to d: d = 2r / sqrt(1 - r^2)
- d to r: r = d / sqrt(d^2 + 4)
- OR to d: d = ln(OR) * sqrt(3) / pi
- Report all conversions performed

## Mandatory Analyses

### 1. Overall Effect Size
Random effects model (DerSimonian-Laird or REML). Report: pooled effect + 95% CI + p-value + z-test.

### 2. Heterogeneity
- Q statistic (homogeneity test)
- I-squared: 25% low, 50% moderate, 75% high
- tau-squared: between-study variance
- Prediction interval

### 3. Publication Bias
- Funnel plot: effect sizes vs SE
- Egger's test: p < 0.10 = bias
- Trim-and-fill: estimates missing studies
- Report adjusted estimate if bias detected

### 4. Moderator Analysis
- Subgroup analysis: categorical moderators
- Meta-regression: continuous moderators
- Report Q-between (subgroup differences)

### 5. Sensitivity Analysis
- Leave-one-out: remove 1 study at a time
- Quality sensitivity: exclude low-quality studies

## Required Output
- Table 1: Characteristics of Included Studies (N, country, method, population, effect, CI)
- Table 2: Overall Results (k, N, effect, CI, p, I-squared, tau-squared)
- Figure 1: Forest Plot (squares = studies, diamond = pooled)
- Figure 2: Funnel Plot (asymmetry = bias)

## AMSTAR 2 Quality Checklist
Protocol registered, 2+ databases searched, excluded studies listed, risk of bias assessed, appropriate statistical method, heterogeneity investigated, publication bias assessed, conflicts declared.
