# BX-Scholar Workflow

Academic research workflow MCP server — 19 tools + 8 prompts + 21 skill resources for the full research lifecycle. The "BaXiJen way".

Part of the [bx-scholar-mcp](https://github.com/leomcamilo/bx-scholar-mcp) monorepo. Depends on [bx-scholar-core](../bx-scholar-core/).

## Quick Start

```bash
export POLITE_EMAIL="you@university.edu"

# Run as MCP server
uvx --from "git+https://github.com/leomcamilo/bx-scholar-mcp#subdirectory=packages/bx-scholar-workflow" bx-scholar-workflow
```

### MCP Client Configuration

```json
{
  "mcpServers": {
    "bx-scholar-workflow": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/leomcamilo/bx-scholar-mcp#subdirectory=packages/bx-scholar-workflow", "bx-scholar-workflow"],
      "env": { "POLITE_EMAIL": "you@university.edu" }
    }
  }
}
```

## What's Included

### Tools (19) — from bx-scholar-core
All 19 core tools are included. See [bx-scholar-core README](../bx-scholar-core/README.md) for the full tools table.

### Prompts (8)

| Prompt | Parameters | Description |
|--------|-----------|-------------|
| `research_pipeline` | — | 13-phase orchestrator from topic to submission |
| `journal_calibrator` | `journal_name` | Journal DNA profiling and strategic positioning |
| `citation_verification` | — | Anti-hallucination gate for citation verification |
| `literature_search` | `topic` | Systematic multi-source search protocol |
| `revise_and_resubmit` | — | R&R protocol with response letter generation |
| `qualitative_analysis` | — | Method selection guide (Gioia, Braun & Clarke, etc.) |
| `theory_development` | — | Theory building, extension, integration |
| `meta_analysis_protocol` | — | Meta-analysis workflow with AMSTAR 2 |

### Skills (21 resources)

Accessible via `skills://name`. Each skill is a detailed markdown protocol for a specific research phase.

| Phase | Skill | Description |
|-------|-------|-------------|
| 0 | `research-pipeline` | Full orchestrator with backtracking |
| 0.5 | `journal-calibrator` | Editor persona and positioning |
| 1 | `discovery` | Topic discovery and validation |
| 2 | `methodology` | Research design |
| 3 | `compliance` | Ethics, IRB/CEP, LGPD, Open Science |
| 4 | `systematic-search` | Autonomous multi-source search |
| 4+ | `prisma` | PRISMA 2020 systematic review |
| 5 | `curation` | Quality curation with real rankings |
| 6 | `paper-reader` | Structured reading notes |
| 7 | `literature-review` | Argumentative literature review |
| 8 | `qualitative-analysis` | Qualitative analysis methods |
| 8+ | `meta-analysis` | Quantitative meta-analysis |
| 9 | `results-discussion` | Results and discussion writing |
| 10 | `academic-writing` | CARS Introduction, Conclusion, Abstract |
| 10+ | `theory-development` | Theoretical contribution |
| 10+ | `conclusion` | Synthesis, contributions, limitations |
| 11 | `internal-review` | Adversarial paper review |
| 12 | `reference-manager` | BibTeX, multi-style formatting |
| 12 | `formatter` | Journal submission formatting |
| 12 | `submission` | Venue selection and package |
| 13 | `revise-resubmit` | R&R response management |

## Configuration

Same as bx-scholar-core. See [core README](../bx-scholar-core/README.md#configuration).

## Development

```bash
cd packages/bx-scholar-workflow
uv sync --extra dev
uv run pytest tests/ -x -q
```

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for full guidelines.

## License

MIT
