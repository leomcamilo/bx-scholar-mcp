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

- **Multi-source search** — OpenAlex, CrossRef, ArXiv, SciELO, Semantic Scholar, Unpaywall, Tavily
- **Journal rankings** — SJR (32K+), Qualis CAPES (170K+), Harzing's JQL (ABS/ABDC/CNRS/FNEGE/VHB)
- **Citation verification** — anti-hallucination pipeline with retraction detection
- **Bibliometrics** — citation networks, co-citation clusters, keyword trends
- **Full-text pipeline** — OA check, PDF download, ML-powered text extraction
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

### Legacy monolith (still works)

```bash
uv run python run_server.py
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

## Ranking Data

Ranking data is not included in the repo. To set up:

1. **JQL** (842 journals) — download the ISSN PDF from [harzing.com](https://harzing.com/resources/journal-quality-list), then: `python scripts/parse_jql.py /path/to/jql.pdf data/jql_rankings.csv`
2. **SJR** (32K journals) — download CSV from [scimagojr.com](https://www.scimagojr.com/journalrank.php), save as `data/sjr_rankings.csv`
3. **Qualis CAPES** (170K entries) — download from [Plataforma Sucupira](https://sucupira.capes.gov.br), save as `data/qualis_capes.xlsx`

The server works without ranking files — ranking tools return `"N/A"` for missing data.

## What makes BX-Scholar different

- **Qualis CAPES** — the only MCP server that indexes Brazilian academic rankings
- **JQL** — ABS, ABDC, CNRS, FNEGE, VHB rankings for business/management schools
- **SciELO** — built-in LATAM/Brazil Open Access coverage
- **Brazilian context** — LGPD compliance, ABNT formatting, ENANPAD/CAPES workflows
- **Anti-hallucination** — verify every citation against CrossRef + OpenAlex before using it

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

**Built by [BaXiJen](https://baxijen.ai)** · Powered by [MCP](https://modelcontextprotocol.io)

</div>
