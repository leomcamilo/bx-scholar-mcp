# Meta-Analysis Protocol

Meta-analysis is the quantitative synthesis of results from independent empirical studies. It is one of the most valued publication formats in top journals.

## When to Use
- At least 5-10 empirical studies on the SAME relationship
- Studies report (or allow calculating) comparable effect sizes
- Goal: estimate the TRUE effect, identify moderators of variability

## Prerequisites
- Systematic search protocol (PRISMA-MA variant)
- Use search_openalex, search_crossref, search_semantic_scholar for comprehensive search
- Use lookup_journal_ranking for quality assessment of included studies

## PRISMA-MA Specific Inclusion Criteria
- Studies MUST report: sample size (N), effect size (d, r, OR, RR), or sufficient data to calculate them
- Define a priori: which effect measure to use (Cohen's d, correlation r, odds ratio)
- Qualitative studies: EXCLUDE (meta-analysis is quantitative)

## Data Extraction Table

For EACH included study:
| Field | Description |
|-------|-------------|
| study_id | Author(s) + Year |
| N | Sample size |
| effect_size | Value (d, r, OR) |
| se | Standard error |
| ci_lower | 95% CI lower bound |
| ci_upper | 95% CI upper bound |
| p_value | p-value (if reported) |
| moderators | Moderator variables (country, method, population, etc.) |
| quality_score | Methodological quality score |

## Effect Size Conversions
- r to d: d = 2r / sqrt(1 - r^2)
- d to r: r = d / sqrt(d^2 + 4)
- OR to d: d = ln(OR) * sqrt(3) / pi
- Always report which conversions were performed

## Mandatory Analyses

### 1. Overall Effect Size
- Model: random effects (DerSimonian-Laird or REML)
- Justification: studies likely measure slightly different effects
- Report: pooled effect size + 95% CI + p-value + z-test

### 2. Heterogeneity
- Q statistic: homogeneity test (significant = heterogeneous)
- I-squared: % of variability attributable to real heterogeneity
  - 25% = low, 50% = moderate, 75% = high
- tau-squared: between-study variance
- Prediction interval: probable range of the true effect in a NEW study

### 3. Publication Bias
- Funnel plot: scatter of effect sizes vs. SE
- Egger's test: funnel asymmetry test (p < 0.10 = bias)
- Trim-and-fill: estimates number of "missing" studies
- If bias detected: report adjusted estimate

### 4. Moderator Analysis
- Subgroup analysis: for categorical moderators
- Meta-regression: for continuous moderators
- Test: country, sample type, year, method, quality
- Report: Q-between (difference between subgroups)

### 5. Sensitivity Analysis
- Leave-one-out: remove 1 study at a time, recalculate
- Influence: does any single study substantially change the result?
- Quality: does the result change if low-quality studies are excluded?

## Required Tables and Figures

**Table 1: Characteristics of Included Studies**
| Study | N | Country | Method | Population | Effect (d) | 95% CI |

**Table 2: Overall Results**
| Analysis | k | N | Effect | 95% CI | p | I-squared | tau-squared |

**Figure 1: Forest Plot** — One square per study (size = weight), horizontal lines = 95% CI, diamond = pooled effect
**Figure 2: Funnel Plot** — Effect size vs. SE, asymmetry = possible publication bias

## Quality Checklist (AMSTAR 2)
- [ ] Protocol registered a priori?
- [ ] Search in at least 2 databases?
- [ ] List of excluded studies with justification?
- [ ] Risk of bias assessment for included studies?
- [ ] Appropriate statistical method (random vs fixed)?
- [ ] Heterogeneity investigated?
- [ ] Publication bias assessed?
- [ ] Conflicts of interest declared?