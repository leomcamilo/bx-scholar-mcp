"""Collect the base works of the verify_citation benchmark.

Freezes, for each work, the complete Crossref and OpenAlex responses (URL,
status, UTC time, SHA-256, body), so the benchmark runs offline and anyone can
reproduce a decision from the same bytes. Works are drawn per stratum with a
fixed seed; admission depends only on the frozen metadata, never on the
verifier.

    uv run python packages/bx-scholar-core/benchmark/collect.py --limit 600

Re-running skips works already on disk. Network access is needed only here.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import re
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

DATA = Path(__file__).parent / "data" / "works"
MAILTO = "bx-scholar-ci@users.noreply.github.com"
UA = f"BX-Scholar-benchmark/0.1 (mailto:{MAILTO})"
SEED = 20261008
OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"

ALLOWED_TYPES = {
    "journal-article", "proceedings-article", "book-chapter", "book", "monograph",
    "posted-content", "report", "dissertation", "edited-book",
}  # fmt: skip
CJK = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯]")
HANGUL = re.compile("[가-힯]")
KANA = re.compile("[぀-ヿ]")
_SHORT_STOP = set(
    [
        "a",
        "an",
        "the",
        "of",
        "on",
        "in",
        "and",
        "for",
        "to",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "e",
        "em",
        "para",
        "o",
        "os",
        "as",
        "la",
        "el",
        "y",
    ]
)

# stratum -> (quota, how to draw candidates, admission predicate name)
STRATA: dict[str, dict] = {
    "pt_br": {"quota": 100, "openalex": "language:pt,has_doi:true,type:article"},
    "zh": {"quota": 30, "openalex": "language:zh,has_doi:true"},
    "ja": {"quota": 25, "openalex": "language:ja,has_doi:true"},
    "ko": {"quota": 25, "openalex": "language:ko,has_doi:true"},
    "chemistry": {"quota": 70, "openalex": "primary_topic.field.id:16,has_doi:true,type:article"},
    "medicine": {"quota": 70, "openalex": "primary_topic.field.id:27,has_doi:true,type:article"},
    "engineering": {"quota": 70, "openalex": "primary_topic.field.id:22,has_doi:true"},
    "institutional": {"quota": 50, "crossref_author_queries": [
        "consortium", "collaboration", "working group", "organization", "ministério",
        "society", "committee", "institute", "agency", "network",
    ]},
    "compound_names": {"quota": 60, "openalex": "language:es|pt,has_doi:true,type:article"},
    "long_11_20": {"quota": 20, "openalex": "authors_count:11-20,has_doi:true,type:article"},
    "long_21_100": {"quota": 20, "openalex": "authors_count:21-99,has_doi:true,type:article"},
    "long_100_plus": {"quota": 20, "openalex": "authors_count:>100,has_doi:true,type:article"},
    "short_titles": {"quota": 40, "openalex": "has_doi:true,type:article"},
}  # fmt: skip


def _freeze(resp: httpx.Response) -> dict:
    body = resp.content
    return {
        "method": "GET",
        "url": str(resp.request.url),
        "status": resp.status_code,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "content_type": resp.headers.get("content-type", ""),
        "body": body.decode("utf-8", errors="replace"),
    }


class Fetcher:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=60, headers={"User-Agent": UA}, follow_redirects=True)

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(4):
            try:
                r = self.client.get(url, params=params)
            except httpx.HTTPError:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(float(r.headers.get("Retry-After", 2 * (attempt + 1))))
                continue
            time.sleep(0.12)  # polite pacing for both APIs
            return r
        return r


def _content_words(title: str) -> int:
    words = [w for w in re.findall(r"[^\W_]+", title.lower()) if len(w) > 1]
    return sum(1 for w in words if w not in _SHORT_STOP)


def _crossref_item(frozen: dict) -> dict | None:
    if frozen["status"] != 200:
        return None
    try:
        return json.loads(frozen["body"])["message"]
    except (ValueError, KeyError):
        return None


def admit(stratum: str, item: dict, oa: dict | None) -> tuple[bool, str]:
    """Admission uses only the frozen metadata, never the verifier."""
    title = (item.get("title") or [""])[0].strip()
    authors = item.get("author") or []
    if not title:
        return False, "no title"
    if item.get("type") not in ALLOWED_TYPES:
        return False, f"type {item.get('type')}"
    if not authors:
        return False, "no authors in Crossref"
    if not any(item.get(k) for k in ("published-print", "published-online", "published", "issued")):
        return False, "no date"
    persons = [a for a in authors if a.get("family")]
    orgs = [a for a in authors if a.get("name") and not a.get("family")]
    if stratum == "institutional":
        return (bool(orgs), "no organization author")
    if not persons:
        return False, "no structured person author"
    if stratum in ("zh", "ja", "ko"):
        if not CJK.search(title):
            return False, "title not in CJK script"
        if stratum == "ko" and not HANGUL.search(title):
            return False, "no hangul"
        if stratum == "ja" and not KANA.search(title):
            return False, "no kana"
        return True, ""
    if stratum == "compound_names":
        ok = any(re.search(r"[\s\-]", a["family"].strip()) for a in persons)
        return ok, "no compound surname"
    if stratum.startswith("long_"):
        n = len(authors)
        lo, hi = {"long_11_20": (11, 20), "long_21_100": (21, 100), "long_100_plus": (101, 10**6)}[
            stratum
        ]
        return lo <= n <= hi, f"{n} authors in Crossref"
    if stratum == "short_titles":
        sub = (item.get("subtitle") or [""])[0]
        return _content_words(title) <= 3 and not sub, "title not short"
    return True, ""


def candidate_dois(f: Fetcher, stratum: str, spec: dict, page: int) -> list[str]:
    if "crossref_author_queries" in spec:
        q = spec["crossref_author_queries"][page % len(spec["crossref_author_queries"])]
        r = f.get(
            CROSSREF,
            {
                "query.author": q,
                "rows": 100,
                "offset": 100 * (page // len(spec["crossref_author_queries"])),
                "select": "DOI",
                "mailto": MAILTO,
            },
        )
        return [i["DOI"] for i in r.json()["message"]["items"]] if r.status_code == 200 else []
    r = f.get(
        OPENALEX,
        {
            "filter": spec["openalex"],
            "sample": 200,
            "seed": SEED + page,
            "per_page": 200,
            "select": "doi",
            "mailto": MAILTO,
        },
    )
    if r.status_code != 200:
        print(f"  openalex {r.status_code}: {r.text[:120]}", file=sys.stderr)
        return []
    return [w["doi"].replace("https://doi.org/", "") for w in r.json()["results"] if w.get("doi")]


def split_of(doi: str) -> str:
    """Development or holdout, by a stable hash of the DOI."""
    return (
        "dev" if int(hashlib.sha256(doi.lower().encode()).hexdigest(), 16) % 2 == 0 else "holdout"
    )


def collect(limit: int) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    have = {p.name for p in DATA.glob("*.json.gz")}
    seen_dois: set[str] = set()
    for p in DATA.glob("*.json.gz"):
        seen_dois.add(json.loads(gzip.decompress(p.read_bytes()))["doi"].lower())
    f = Fetcher()
    total = 0
    for stratum, spec in STRATA.items():
        quota = min(spec["quota"], max(0, limit - total))
        done = sum(1 for n in have if n.startswith(stratum + "__"))
        page = 0
        rng = random.Random(SEED + zlib.crc32(stratum.encode()))
        while done < quota and page < 40:
            dois = candidate_dois(f, stratum, spec, page)
            rng.shuffle(dois)
            page += 1
            for doi in dois:
                if done >= quota:
                    break
                if doi.lower() in seen_dois:
                    continue
                cr = _freeze(f.get(f"{CROSSREF}/{quote(doi, safe='/')}", {"mailto": MAILTO}))
                item = _crossref_item(cr)
                if item is None:
                    continue
                oa = _freeze(f.get(f"{OPENALEX}/https://doi.org/{doi}", {"mailto": MAILTO}))
                ok, _why = admit(stratum, item, None)
                if not ok:
                    continue
                seen_dois.add(doi.lower())
                done += 1
                wid = f"{stratum}__{done:03d}"
                record = {
                    "id": wid,
                    "stratum": stratum,
                    "doi": doi,
                    "split": split_of(doi),
                    "http": {"crossref_work": cr, "openalex_work": oa},
                }
                (DATA / f"{wid}.json.gz").write_bytes(
                    gzip.compress(json.dumps(record, ensure_ascii=False).encode())
                )
                print(f"{wid} {doi} {(item.get('title') or [''])[0][:60]}")
        total += done
        print(f"== {stratum}: {done}/{spec['quota']}", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=600)
    collect(ap.parse_args().limit)
