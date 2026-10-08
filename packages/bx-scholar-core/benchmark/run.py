"""Run the verify_citation benchmark offline and count errors.

Every case goes through the real tool path (_verify_one: Crossref and OpenAlex
clients, parsing, decision) with HTTP simulated from the frozen records. The
simulator answers only the two search requests the verifier makes and fails
on anything else, so no case can silently reach the network.

Error definition (agreed with the Codex review):
- one error per case, however many fields it gets wrong;
- false positive: verified when the expected status is not "verified", or
  verified with a DOI other than the expected one;
- false negative: expected "verified", got anything else;
- wrong status: the other mismatches (e.g. conflict expected, insufficient got);
- contract: expected confidence not met.
Gate: errors <= 4, false positives = 0.

    uv run python packages/bx-scholar-core/benchmark/run.py --split dev
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

import httpx
from aiolimiter import AsyncLimiter

from bx_scholar_core.clients.pool import ClientPool
from bx_scholar_core.config import Settings
from bx_scholar_core.logging import setup_logging
from bx_scholar_core.tools.verify import _verify_one

ROOT = Path(__file__).parent
MAX_ERRORS = 4


def load_raw() -> dict[str, dict]:
    raw = {}
    for p in sorted((ROOT / "data" / "works").glob("*.json.gz")):
        rec = json.loads(gzip.decompress(p.read_bytes()))
        cr = json.loads(rec["http"]["crossref_work"]["body"])["message"]
        oa_http = rec["http"]["openalex_work"]
        oa = json.loads(oa_http["body"]) if oa_http["status"] == 200 else None
        raw[rec["id"]] = {"crossref": cr, "openalex": oa, "doi": rec["doi"]}
    return raw


def responses(case: dict, raw: dict[str, dict]) -> tuple[dict, dict]:
    r = case["retrieval"]
    cr_items, oa_results = [], []
    for wid in r.get("crossref", []):
        item = copy.deepcopy(raw[wid]["crossref"])
        if r.get("strip_authors") and wid == case["work"]:
            item.pop("author", None)
        cr_items.append(item)
    for wid in r.get("openalex", []):
        work = copy.deepcopy(raw[wid]["openalex"])
        if work is None:
            continue
        if r.get("strip_authors") and wid == case["work"]:
            work["authorships"] = []
        oa_results.append(work)
    if r.get("duplicate"):
        clone_doi = f"10.99999/clone.{case['work']}"
        item = copy.deepcopy(raw[case["work"]]["crossref"])
        item["DOI"] = clone_doi
        cr_items.append(item)
        work = copy.deepcopy(raw[case["work"]]["openalex"])
        if work:
            work["doi"] = f"https://doi.org/{clone_doi}"
            work["id"] = "https://openalex.org/W0"
            work["ids"] = {"openalex": work["id"], "doi": work["doi"]}
            oa_results.append(work)
    return (
        {"status": "ok", "message": {"items": cr_items}},
        {"results": oa_results, "meta": {"count": len(oa_results)}},
    )


def _transport(
    body: dict, host: str, param: str, expected: str, refusals: list[str]
) -> httpx.MockTransport:
    """Answers exactly the search the verifier must make: GET, https, this host
    and path, the expected query text, and no year filter. Anything else is
    refused and recorded, so it fails the case even if the verifier recovers."""

    def handler(req: httpx.Request) -> httpx.Response:
        ok = (
            req.method == "GET"
            and req.url.scheme == "https"
            and req.url.host == host
            and req.url.path == "/works"
            and req.url.params.get(param) == expected
            and "filter" not in req.url.params
        )
        if ok:
            return httpx.Response(200, json=body)
        refusals.append(f"{req.method} {req.url}")
        raise AssertionError(f"unregistered request: {req.method} {req.url}")

    return httpx.MockTransport(handler)


def classify(case: dict, result: dict) -> str | None:
    exp, got = case["expected_status"], result["status"]
    doi = (result.get("match") or {}).get("doi", "").lower()
    if got == "verified" and (exp != "verified" or doi != case["expected_doi"].lower()):
        return "false_positive"
    if exp == "verified" and got != "verified":
        return "false_negative"
    if exp != got:
        return "wrong_status"
    if case.get("expected_confidence") and result.get("confidence") != case["expected_confidence"]:
        return "contract"
    return None


async def run(split: str, limit: int | None, verbose: bool) -> dict:
    setup_logging(level="CRITICAL")
    raw = load_raw()
    cases = [
        json.loads(line) for line in (ROOT / "data" / f"cases_{split}.jsonl").open() if line.strip()
    ]
    if limit:
        cases = cases[:limit]
    pool = ClientPool(Settings(polite_email="bench@bx-scholar.dev", cache_enabled=False))
    for client in (pool.crossref, pool.openalex):
        client._limiter = AsyncLimiter(100_000, 1)  # simulated HTTP: no need to throttle
    errors, by_kind = [], Counter()
    by_transformation: dict[str, Counter] = {}
    for case in cases:
        cr_body, oa_body = responses(case, raw)
        refusals: list[str] = []
        cr_query = f"{case['author']} {case['title']}".strip()
        pool.crossref._client = httpx.AsyncClient(
            transport=_transport(
                cr_body, "api.crossref.org", "query.bibliographic", cr_query, refusals
            )
        )
        pool.openalex._client = httpx.AsyncClient(
            transport=_transport(oa_body, "api.openalex.org", "search", case["title"], refusals)
        )
        result = await _verify_one(
            pool.crossref, pool.openalex, case["author"], case["year"], case["title"],
            case["title_mode"],
        )  # fmt: skip
        # a request the simulator refused is a harness failure, counted on its own
        kind = "harness" if refusals or result.get("source_errors") else classify(case, result)
        t = by_transformation.setdefault(case["transformation"], Counter())
        t["cases"] += 1
        if kind:
            by_kind[kind] += 1
            t[kind] += 1
            closest = result.get("closest_match") or {}
            errors.append({
                "case": case["id"], "kind": kind, "transformation": case["transformation"],
                "expected": case["expected_status"], "got": result["status"],
                "author": case["author"], "year": case["year"], "title": case["title"],
                "title_mode": case["title_mode"],
                "got_doi": (result.get("match") or closest.get("match") or {}).get("doi", ""),
                "expected_doi": case["expected_doi"],
                "reasons": closest.get("rejected_because") or result.get("warnings") or [],
                "source_errors": result.get("source_errors"),
                "refused_requests": refusals,
            })  # fmt: skip
    await pool.aclose()
    total_errors = len(errors)
    summary = {
        "split": split,
        "cases": len(cases),
        "errors": total_errors,
        "by_kind": dict(by_kind),
        "gate": {
            "max_errors": MAX_ERRORS,
            "passed": total_errors <= MAX_ERRORS and by_kind["false_positive"] == 0,
        },
        "by_transformation": {k: dict(v) for k, v in sorted(by_transformation.items())},
    }
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / f"{split}.json").write_text(
        json.dumps({"summary": summary, "errors": errors}, ensure_ascii=False, indent=1)
    )
    return {"summary": summary, "errors": errors}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "holdout"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    report = asyncio.run(run(args.split, args.limit, args.verbose))
    s = report["summary"]
    print(f"{s['split']}: {s['cases']} cases, {s['errors']} errors {s['by_kind']}")
    for t, c in s["by_transformation"].items():
        bad = {k: v for k, v in c.items() if k != "cases"}
        if bad:
            print(f"  {t:28} {c['cases']:5} cases  {bad}")
    print("GATE", "PASSED" if s["gate"]["passed"] else "FAILED")
    sys.exit(0 if s["gate"]["passed"] else 1)


if __name__ == "__main__":
    main()
