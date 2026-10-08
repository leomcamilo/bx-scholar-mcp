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


def test_classify_rejects_incoherent_and_counts_false_positive_first() -> None:
    """Codex cycle 5: a verified answer with the right DOI but another record's
    title and authors is an error; a wrong DOI is a false positive even when the
    answer is also incoherent."""
    import gzip
    import json

    run = _run_module()
    raw = run.load_raw()
    with gzip.open(BENCH / "data" / "cases_dev.jsonl.gz", "rt", encoding="utf-8") as f:
        case = next(c for c in map(json.loads, f) if c["expected_status"] == "verified")
    work = raw[case["work"]]
    year = case["year"]
    good = {
        "verified": True,
        "status": "verified",
        "confidence": case.get("expected_confidence") or "high",
        "checks": {"title_match": "full", "author_match": "exact", "year_match": "exact"},
        "match": {"doi": case["expected_doi"], "title": "Unrelated paper", "year": year,
                  "authors": [{"name": "Unknown Person"}]},
    }  # fmt: skip
    assert run.classify(case, good, work) == "inconsistent"
    wrong = json.loads(json.dumps(good))
    wrong["match"]["doi"] = "10.9999/wrong"
    wrong["checks"]["author_match"] = "conflict"
    assert run.classify(case, wrong, work) == "false_positive"


def test_classify_checks_the_returned_record_against_the_frozen_one() -> None:
    """Codex cycle 6: a verified answer with the work's DOI, year and checks but
    another author, a changed sign in the title or an invented subtitle is
    inconsistent."""
    import asyncio

    run = _run_module()
    raw = run.load_raw()
    case = {"id": "chemistry__002#01", "author": "", "year": None}
    work = raw["chemistry__002"]
    report = asyncio.run(run.run("dev", limit=1, verbose=False))
    assert report["summary"]["errors"] == 0
    cr = work["crossref"]
    title = cr["title"][0]
    authors = [{"name": f"{a.get('given', '')} {a.get('family', '')}".strip()}
               for a in cr.get("author") or []]  # fmt: skip
    base = {
        "verified": True, "status": "verified", "confidence": "high",
        "checks": {"title_match": "full", "author_match": "exact", "year_match": "not_given"},
        "match": {"doi": work["doi"], "title": title, "year": next(iter(run._frozen(work)[2])),
                  "authors": authors},
    }  # fmt: skip
    case = {**case, "expected_status": "verified"}
    assert run.classify(case, base, work) is None
    for change in (
        {"authors": [{"name": "John Davies"}]},
        {"title": title + " +"},
        {"subtitle": "Fabricated independent clinical trial"},
    ):
        bad = {**base, "match": {**base["match"], **change}}
        assert run.classify(case, bad, work) == "inconsistent", change
