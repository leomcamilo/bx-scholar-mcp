# Contributing to BX-Scholar MCP

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)

## Setup

```bash
git clone https://github.com/leomcamilo/bx-scholar-mcp.git
cd bx-scholar-mcp
uv sync --extra dev
```

## Project structure

This is a monorepo with two publishable packages:

```
packages/
  bx-scholar-core/      # Search, rankings, verification, bibliometrics
  bx-scholar-workflow/   # Prompts, skills, orchestrators
```

The root `run_server.py` is the legacy monolith — still functional but being migrated.

## Development workflow

### Running the legacy server

```bash
uv run python run_server.py
```

### Running a package server

```bash
# Core
cd packages/bx-scholar-core
uv run bx-scholar-core

# Workflow
cd packages/bx-scholar-workflow
uv run bx-scholar-workflow
```

### Linting

```bash
uv run ruff check .          # find issues
uv run ruff check --fix .    # auto-fix
uv run ruff format .         # format code
```

### Testing

```bash
# All tests
uv run pytest

# Core only
uv run pytest packages/bx-scholar-core/tests/ -v

# Workflow only
uv run pytest packages/bx-scholar-workflow/tests/ -v

# With coverage
uv run pytest packages/bx-scholar-core/tests/ --cov=bx_scholar_core --cov-report=term-missing
```

### Type checking

```bash
uv run mypy packages/bx-scholar-core/src/
```

## Commit conventions

We use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — new feature
- `fix:` — bug fix
- `refactor:` — code change that neither fixes a bug nor adds a feature
- `test:` — adding or updating tests
- `docs:` — documentation only
- `chore:` — maintenance (CI, deps, config)

One commit = one logical change. Keep commits small and focused.

## Architecture Decision Records

Non-obvious trade-offs go in `docs/adr/NNNN-title.md`. Use the template:

```markdown
# NNNN: Title

## Status
Proposed | Accepted | Deprecated | Superseded

## Context
What is the issue we're seeing that motivates this decision?

## Decision
What is the change we're making?

## Consequences
What becomes easier/harder as a result?
```

## Adding dependencies

New dependencies require justification. Before adding one:

1. Check if the standard library or an existing dep can do the job
2. Verify the license is compatible (MIT, Apache-2.0, BSD, ISC)
3. Write a short ADR if the dependency is significant

## Code style

- Type hints everywhere (`from __future__ import annotations` at the top)
- No `print()` in library code — use `structlog`
- No `pandas` in core hot paths — use `csv` module or plain dicts
- Tests before or alongside code, never "later"
