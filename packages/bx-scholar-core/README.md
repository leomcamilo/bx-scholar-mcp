# BX-Scholar Core

Academic research MCP server — search, rankings, citation verification, bibliometrics, full-text pipeline.

Part of the [bx-scholar-mcp](https://github.com/leomcamilo/bx-scholar-mcp) monorepo.

## Install

```bash
# From GitHub
uvx --from "git+https://github.com/leomcamilo/bx-scholar-mcp#subdirectory=packages/bx-scholar-core" bx-scholar-core

# From local clone
cd packages/bx-scholar-core
uv run bx-scholar-core
```

## Configuration

| Variable | Required | Description |
|----------|:--------:|-------------|
| `POLITE_EMAIL` | **Yes** | Email for polite API pools (OpenAlex, CrossRef, Unpaywall) |
| `TAVILY_API_KEY` | No | Tavily web search |
| `S2_API_KEY` | No | Semantic Scholar (higher rate limits) |

See the [monorepo README](../../README.md) for full documentation.
