"""Fast slice of the verify_citation benchmark, run in CI.

The full run (about 12k cases, ~10 min per split) is
`uv run python packages/bx-scholar-core/benchmark/run.py --split dev|holdout`.
This slice catches regressions in the verifier on every pull request.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

BENCH = Path(__file__).parents[2] / "benchmark"


def _run_module():
    spec = importlib.util.spec_from_file_location("bx_benchmark_run", BENCH / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_first_cases_of_dev_split_pass() -> None:
    run = _run_module()
    report = await run.run("dev", limit=300, verbose=False)
    s = report["summary"]
    assert s["by_kind"].get("false_positive", 0) == 0, report["errors"]
    assert s["errors"] == 0, report["errors"]
