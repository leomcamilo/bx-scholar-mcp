"""Smart ID resolution — normalize DOI, arXiv, OpenAlex, Semantic Scholar IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IDType = Literal["doi", "arxiv", "openalex", "s2", "unknown"]

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:")
_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_OPENALEX_RE = re.compile(r"^W\d+$", re.IGNORECASE)


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
    """
    s = raw.strip()

    # DOI detection
    for prefix in _DOI_PREFIXES:
        if s.lower().startswith(prefix.lower()):
            return ResolvedID(id_type="doi", value=s[len(prefix) :], raw=raw)
    if s.startswith("10.") and "/" in s:
        return ResolvedID(id_type="doi", value=s, raw=raw)

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
