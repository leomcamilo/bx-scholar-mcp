<div align="center">

# BX-Scholar MCP

**Your AI agent's academic research toolkit**

Search 250M+ papers. Verify every citation. Never hallucinate a reference again.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-8B5CF6.svg)](https://modelcontextprotocol.io)
[![OpenAlex](https://img.shields.io/badge/OpenAlex-250M+_papers-E5543E.svg)](https://openalex.org)
[![arXiv](https://img.shields.io/badge/arXiv-Grey_Literature-B31B1B.svg)](https://arxiv.org)

---

</div>

## Packages

This monorepo contains two publishable packages:

### [`bx-scholar-core`](packages/bx-scholar-core/)

Infrastructure for academic search, rankings, and verification. Enxuto, testado, rapido.

- **Multi-source search.** One `search_papers` call reaches OpenAlex, CrossRef, ArXiv, Semantic Scholar, Europe PMC, CORE, SciELO Brasil, BDTD, OasisBR, LA Referencia, CiNii Research, J-STAGE and Tavily, with presets for Brazil (`br`), Latin America (`latam`), Japan (`asia`), health (`bio`) and open access (`oa`). `search_theses` covers Brazilian and Latin American theses.
- **Journal rankings.** SJR (32K+), Qualis CAPES (170K+), Harzing's JQL (ABS/ABDC/CNRS/FNEGE/VHB).
- **Citation verification.** `verify_citation` confirms a reference only when exactly one work matches its title, every cited author and the year; otherwise it says whether the citation conflicts with a record, matches several works or lacks the data to decide. Retractions are checked through Crossref.
- **Bibliometrics.** Citation networks, snowballing and citing/cited lists merged from OpenAlex and OpenCitations, co-citation clusters, keyword trends.
- **Full text.** `get_fulltext` returns open-access text by section from Europe PMC or CORE without a PDF; for the rest, OA check (Unpaywall, then CORE), PDF download and text extraction.
- **Cache** — DuckDB-backed persistent cache with configurable TTLs
- **Rate limiting** — per-source limits with retry and backoff

### [`bx-scholar-workflow`](packages/bx-scholar-workflow/)

Opinionated academic research workflows — the BaXiJen way. Depends on `bx-scholar-core`.

- **8 prompts** — research pipeline, journal calibrator, citation verification, literature search, R&R, qualitative analysis, theory development, meta-analysis
- **21 skill resources** — PRISMA, CARS model, Gioia method, Braun & Clarke, and more
- **Orchestrators** — composite tools that chain core tools into complete workflows

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

### Install and run

```bash
git clone https://github.com/leomcamilo/bx-scholar-mcp.git
cd bx-scholar-mcp
cp .env.example .env    # edit with your email
uv sync
```

### Run the core server

```bash
cd packages/bx-scholar-core
uv run bx-scholar-core
```

### Run the workflow server (includes core)

```bash
cd packages/bx-scholar-workflow
uv run bx-scholar-workflow
```

## Configuration

### MCP Client Setup

**Claude Code:**
```bash
claude mcp add bx-scholar-core -- uv run --directory /path/to/packages/bx-scholar-core bx-scholar-core
```

**Claude Desktop / Cursor:**
```json
{
  "mcpServers": {
    "bx-scholar-core": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/packages/bx-scholar-core", "bx-scholar-core"],
      "env": { "POLITE_EMAIL": "your@email.com" }
    }
  }
}
```

### Environment Variables

| Variable | Required | Description |
|----------|:--------:|-------------|
| `POLITE_EMAIL` | **Yes** | Email for polite API pools (OpenAlex, CrossRef, Unpaywall) |
| `TAVILY_API_KEY` | No | [Tavily](https://tavily.com) web search |
| `S2_API_KEY` | No | [Semantic Scholar](https://www.semanticscholar.org/product/api#api-key-form) — higher rate limits |
| `OPENALEX_API_KEY` | No | [OpenAlex](https://openalex.org/settings/api) (free). Without it, OpenAlex limits requests by a daily budget shared per IP |
| `OPENCITATIONS_TOKEN` | No | [OpenCitations](https://opencitations.net/accesstoken) (free). Raises the rate limit |
| `CORE_API_KEY` | No | [CORE](https://core.ac.uk/services/api) (free). Search works without it; `get_fulltext` reads CORE's extracted text only with a key |
| `CINII_APPID` | No | [CiNii Research](https://support.nii.ac.jp/en/cinii/api/developer) application ID (free). Search works without it |
| `BX_SCHOLAR_HOME` | No | Directory holding `.env` and `data/`. Default: the repo root, found by walking up from the working directory |
| `BX_SCHOLAR_DATA_DIR` | No | Ranking data directory. Relative paths resolve against `BX_SCHOLAR_HOME` (default: `data/`) |

The `.env` and `data/` are always read from the repo root, so `uv run --directory packages/<pkg>` works without copying them. Outside the repo (e.g. a pip install), set `BX_SCHOLAR_HOME`.

## Ranking Data

Ranking data is not included in the repo. To set up:

1. **JQL** (842 journals) — download the ISSN PDF from [harzing.com](https://harzing.com/resources/journal-quality-list), then: `python scripts/parse_jql.py /path/to/jql.pdf data/jql_rankings.csv`
2. **SJR** (32K journals) — download CSV from [scimagojr.com](https://www.scimagojr.com/journalrank.php), save as `data/sjr_rankings.csv`
3. **Qualis CAPES** (170K entries) — download from [Plataforma Sucupira](https://sucupira.capes.gov.br), save as `data/qualis_capes.xlsx`

The server works without ranking files — ranking tools return `"N/A"` for missing data.

## What makes BX-Scholar different

- **Qualis CAPES** — the only MCP server that indexes Brazilian academic rankings
- **JQL** — ABS, ABDC, CNRS, FNEGE, VHB rankings for business/management schools
- **Brazil, Latin America and Japan.** SciELO Brasil, BDTD, OasisBR, LA Referencia, CiNii Research and J-STAGE hold theses and articles that OpenAlex barely indexes. OpenAlex has about 52K Brazilian theses; BDTD lists over 1.1M records (October 2026).
- **Brazilian context** — LGPD compliance, ABNT formatting, ENANPAD/CAPES workflows
- **Anti-hallucination.** Verify every citation against CrossRef + OpenAlex before using it. The verifier runs in CI against a frozen benchmark of 600 real works and about 12K labeled citations.
- **Free academic sources.** Every academic source works without paying, and their API keys are free and optional. The one paid service, Tavily web search, stays off unless you set its key.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

**Built by [BaXiJen](https://baxijen.com.br)** · Powered by [MCP](https://modelcontextprotocol.io)

</div>
