"""Normalização e identidade de identificadores acadêmicos — fonte única.

Antes deste módulo a normalização estava espalhada e divergente no repositório:

- ISSN normalizado em **quatro** lugares (``models/paper.py:91``,
  ``rankings/service.py:18``, ``rankings/sjr.py:14`` e, de fato, no loader do
  Qualis), cada um com sua própria ideia de hífen e caixa;
- DOI normalizado no validador do ``Paper``, no ``id_resolver`` e ainda com
  ``.replace("https://doi.org/", "")`` inline em ``tools/verify.py:86``,
  ``tools/cite.py:77`` e ``tools/cite.py:129``;
- identidade de obra definida **duas** vezes e de formas incompatíveis:
  ``dedup.py`` (DOI → título+ano) e ``tools/snowball.py:43`` (DOI → OpenAlex →
  título minúsculo);
- nenhum suporte a PMID, PMCID, ORCID ou ISBN, e ``_ARXIV_RE`` só reconhecia o
  formato novo — ``math.GT/0309136`` e afins caíam em ``unknown``.

Tudo aqui é determinístico e testável. **Não entra LLM nesta camada** — é
exatamente o que separa "o DOI existe" (fato computacional) de "o artigo
sustenta a afirmação" (julgamento).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

IDType = Literal[
    "doi", "pmid", "pmcid", "arxiv", "openalex", "s2", "issn", "orcid", "isbn", "unknown"
]

# --------------------------------------------------------------------------
# DOI
# --------------------------------------------------------------------------

_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
    "doi.org/",
)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


def normalize_doi(raw: str | None) -> str:
    """DOI canônico: sem prefixo de resolver, minúsculo, sem pontuação final.

    DOIs são case-insensitive na prática (a norma manda tratar assim), então
    minúsculo é a única forma de fazer igualdade e chave primária funcionarem.
    Pontuação terminal é lixo comum de PDF: ``10.1234/abc.`` colado da referência.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    for prefix in _DOI_PREFIXES:
        if s.lower().startswith(prefix):
            s = s[len(prefix) :]
            break
    s = s.strip().rstrip(".,;)]>")
    if not s.lower().startswith("10."):
        return ""
    return s.lower()


def is_doi(raw: str | None) -> bool:
    return bool(_DOI_RE.match(normalize_doi(raw)))


# --------------------------------------------------------------------------
# PubMed / PMC — inexistentes no v1
# --------------------------------------------------------------------------

_PMID_RE = re.compile(r"^\d{1,8}$")


def normalize_pmid(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip().upper()
    for prefix in ("PMID:", "PUBMED:", "PMID"):
        if s.startswith(prefix):
            s = s[len(prefix) :].strip()
            break
    s = s.lstrip(":").strip()
    return s if _PMID_RE.match(s) else ""


def normalize_pmcid(raw: str | None) -> str:
    """PMCID canônico com o prefixo ``PMC`` — ``PMC1234567``.

    Europe PMC aceita as duas formas; guardamos com prefixo porque é a forma que
    aparece nos registros e evita confundir com PMID em inspeção manual.
    """
    if not raw:
        return ""
    s = str(raw).strip().upper().replace(" ", "")
    if s.startswith("PMCID:"):
        s = s[6:]
    if s.startswith("PMC"):
        s = s[3:]
    s = s.lstrip(":")
    return f"PMC{s}" if s.isdigit() else ""


# --------------------------------------------------------------------------
# arXiv — agora com o formato antigo
# --------------------------------------------------------------------------

# Novo (pós-2007): 2401.12345 / 2401.12345v2
_ARXIV_NEW_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
# Antigo (pré-2007): math.GT/0309136, hep-th/9901001v3, cond-mat.stat-mech/0512028
_ARXIV_OLD_RE = re.compile(r"^([a-z-]+(?:\.[A-Za-z-]+)?/\d{7})(v\d+)?$", re.IGNORECASE)


def normalize_arxiv(raw: str | None) -> str:
    """arXiv ID sem versão. Cobre os formatos novo e antigo."""
    if not raw:
        return ""
    s = str(raw).strip()
    if "arxiv.org/abs/" in s.lower():
        s = s.lower().split("arxiv.org/abs/")[-1]
    if s.lower().startswith("arxiv:"):
        s = s[6:]
    s = s.strip()
    m = _ARXIV_NEW_RE.match(s)
    if m:
        return m.group(1)
    m = _ARXIV_OLD_RE.match(s)
    if m:
        return m.group(1).lower()
    return ""


# --------------------------------------------------------------------------
# OpenAlex / Semantic Scholar
# --------------------------------------------------------------------------

_OPENALEX_RE = re.compile(r"^W\d+$", re.IGNORECASE)
_S2_RE = re.compile(r"^[0-9a-f]{40}$")


def normalize_openalex(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if "openalex.org/" in s:
        s = s.split("openalex.org/")[-1]
    s = s.strip().upper()
    return s if _OPENALEX_RE.match(s) else ""


def normalize_s2(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip().lower()
    if s.startswith("corpusid:"):
        return s
    return s if _S2_RE.match(s) else ""


# --------------------------------------------------------------------------
# ISSN / ORCID / ISBN
# --------------------------------------------------------------------------

_ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dX]$")


def normalize_issn(raw: str | None) -> str:
    """ISSN canônico ``NNNN-NNNC`` (C pode ser X). Substitui as quatro cópias.

    Retorna string vazia em entrada inválida em vez de devolver o lixo original
    — que era o comportamento de ``models/paper.py:91``, e que fazia um ISSN
    malformado virar chave de lookup e nunca casar com nada.
    """
    if not raw:
        return ""
    s = re.sub(r"[^0-9Xx]", "", str(raw)).upper()
    if len(s) != 8:
        return ""
    s = f"{s[:4]}-{s[4:]}"
    return s if _ISSN_RE.match(s) else ""


def normalize_orcid(raw: str | None) -> str:
    if not raw:
        return ""
    s = re.sub(r"[^0-9Xx]", "", str(raw)).upper()
    if len(s) != 16:
        return ""
    return f"{s[0:4]}-{s[4:8]}-{s[8:12]}-{s[12:16]}"


def normalize_isbn(raw: str | None) -> str:
    if not raw:
        return ""
    s = re.sub(r"[^0-9Xx]", "", str(raw)).upper()
    return s if len(s) in (10, 13) else ""


# --------------------------------------------------------------------------
# Resolução genérica
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedID:
    id_type: IDType
    value: str
    raw: str

    @property
    def is_known(self) -> bool:
        return self.id_type != "unknown"


def resolve_id(raw: str) -> ResolvedID:
    """Descobre o tipo de um identificador solto e o normaliza.

    Ordem importa: o que é inequívoco vem primeiro. PMID fica por último entre
    os numéricos porque "1234567" sozinho é ambíguo — só é tratado como PMID se
    vier marcado (``PMID:1234567``) ou se nada mais casar.
    """
    s = (raw or "").strip()
    if not s:
        return ResolvedID("unknown", "", raw)

    if (v := normalize_doi(s)) and is_doi(v):
        return ResolvedID("doi", v, raw)
    if v := normalize_pmcid(s):
        return ResolvedID("pmcid", v, raw)
    if (v := normalize_arxiv(s)):
        return ResolvedID("arxiv", v, raw)
    if (v := normalize_openalex(s)):
        return ResolvedID("openalex", v, raw)
    if (v := normalize_s2(s)):
        return ResolvedID("s2", v, raw)
    if (v := normalize_orcid(s)):
        return ResolvedID("orcid", v, raw)
    if _ISSN_RE.match(s.strip().upper()) and (v := normalize_issn(s)):
        return ResolvedID("issn", v, raw)
    if (v := normalize_pmid(s)):
        return ResolvedID("pmid", v, raw)

    return ResolvedID("unknown", s, raw)


# --------------------------------------------------------------------------
# Identidade de obra — UMA definição
# --------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_title(raw: str | None) -> str:
    """Título normalizado para comparação: sem acento, sem pontuação, minúsculo.

    Remover acento importa para o corpus brasileiro: a mesma obra aparece como
    "Educação" no SciELO e "Educacao" em base que perdeu o encoding.
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKD", str(raw))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _PUNCT_RE.sub(" ", s.lower())
    return _WS_RE.sub(" ", s).strip()


def work_key(
    *,
    doi: str | None = None,
    pmid: str | None = None,
    pmcid: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
    s2_id: str | None = None,
    title: str | None = None,
    year: int | None = None,
) -> str:
    """Chave canônica e estável de uma obra.

    Precedência por confiabilidade do identificador. O fallback por título
    inclui o ano no hash: dois trabalhos homônimos de anos diferentes são obras
    diferentes, e sem o ano o hash os fundiria silenciosamente.

    Devolve ``""`` quando não há nem identificador nem título — o chamador
    precisa decidir o que fazer, e devolver uma chave inventada seria pior.
    """
    if v := normalize_doi(doi):
        return f"doi:{v}"
    if v := normalize_pmcid(pmcid):
        return f"pmcid:{v}"
    if v := normalize_pmid(pmid):
        return f"pmid:{v}"
    if v := normalize_arxiv(arxiv_id):
        return f"arxiv:{v}"
    if v := normalize_openalex(openalex_id):
        return f"openalex:{v}"
    if v := normalize_s2(s2_id):
        return f"s2:{v}"
    if t := normalize_title(title):
        digest = hashlib.sha1(f"{t}|{year or ''}".encode()).hexdigest()[:16]
        return f"title:{digest}"
    return ""
