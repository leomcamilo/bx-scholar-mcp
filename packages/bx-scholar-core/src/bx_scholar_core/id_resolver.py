"""Smart ID resolution — normalize DOI, arXiv, OpenAlex, Semantic Scholar IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IDType = Literal["doi", "arxiv", "openalex", "s2", "pmid", "pmcid", "unknown"]

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:")
_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_OPENALEX_RE = re.compile(r"^W\d+$", re.IGNORECASE)
# A DOI safe to put inside a quoted query: no whitespace, quote or backslash.
# The prefix may be subdivided (10.1000.10/123); parentheses, "<>;#?" stay, as
# real DOIs use them (10.1016/S0140-6736(20)30183-5). Code that puts a DOI in a
# URL path must percent-encode it.
DOI_RE = re.compile(r'10\.\d{4,9}(?:\.\d+)*/[^\s"\\]+')
_PMCID_RE = re.compile(r"(?:^|/)(PMC\d+)/?$", re.IGNORECASE)
# A bare number is ambiguous, so a PMID needs a "pmid:" prefix or a PubMed URL.
_PMID_RE = re.compile(r"^(?:pmid:\s*|https?://pubmed\.ncbi\.nlm\.nih\.gov/)(\d+)/?$", re.I)


@dataclass
class ResolvedID:
    """A normalized identifier with its type."""

    id_type: IDType
    value: str  # normalized value (DOI without prefix, arXiv without version, etc.)
    raw: str  # original input


def resolve_id(raw: str) -> ResolvedID:
    """Resolve an academic paper identifier to its canonical form.

    Accepts:
    - DOI: "10.1234/test", "https://doi.org/10.1234/test", "doi:10.1234/test"
    - ArXiv: "2401.12345", "2401.12345v2", "arXiv:2401.12345"
    - OpenAlex: "W12345", "https://openalex.org/W12345"
    - Semantic Scholar: "abc123def456" (40-char hex)
    - PMCID: "PMC1234567" or a PMC article URL
    - PMID: "pmid:31398324" or a pubmed.ncbi.nlm.nih.gov URL
    """
    s = raw.strip()

    # DOI detection
    for prefix in _DOI_PREFIXES:
        if s.lower().startswith(prefix.lower()):
            return ResolvedID(id_type="doi", value=s[len(prefix) :], raw=raw)
    if s.startswith("10.") and "/" in s:
        return ResolvedID(id_type="doi", value=s, raw=raw)

    m = _PMCID_RE.search(s)
    if m:
        return ResolvedID(id_type="pmcid", value=m.group(1).upper(), raw=raw)
    m = _PMID_RE.match(s)
    if m:
        return ResolvedID(id_type="pmid", value=m.group(1), raw=raw)

    # ArXiv detection
    arxiv_value = s
    if arxiv_value.lower().startswith("arxiv:"):
        arxiv_value = arxiv_value[6:]
    m = _ARXIV_RE.match(arxiv_value)
    if m:
        return ResolvedID(id_type="arxiv", value=m.group(1), raw=raw)

    # OpenAlex detection
    oa_value = s
    if "openalex.org/" in oa_value:
        oa_value = oa_value.split("openalex.org/")[-1]
    if _OPENALEX_RE.match(oa_value):
        return ResolvedID(id_type="openalex", value=oa_value.upper(), raw=raw)

    # Semantic Scholar (40-char hex hash)
    if len(s) == 40 and all(c in "0123456789abcdef" for c in s.lower()):
        return ResolvedID(id_type="s2", value=s, raw=raw)

    return ResolvedID(id_type="unknown", value=s, raw=raw)


def is_valid_doi(value: str) -> bool:
    return bool(DOI_RE.fullmatch(value))
