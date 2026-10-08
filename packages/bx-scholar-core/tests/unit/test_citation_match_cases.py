"""Regression table for citation_match, from three Codex (gpt-6-astra) reviews.

Each row: (case, record, cited author, title fragment, verified?). The record
title and year always agree with the query unless the case is about the title,
so the row isolates one decision. Rows marked R1-R3 reproduce a defect a review
found; the rest are controls that keep legitimate citations verified.
"""

from __future__ import annotations

import pytest

from bx_scholar_core.citation_match import score_candidate
from bx_scholar_core.models.paper import Author, Paper


def P(t: str, a: list[str], y: int = 2020) -> Paper:
    return Paper(title=t, authors=[Author(name=x) for x in a], year=y)


T = "Urban mobility prediction with graph networks"
F = "urban mobility prediction graph networks"
EN = "\u2013"
EM = "\u2014"

CASES = [
    # round 3 P1 false positives
    ("R3-1 John Jones vs John J. Smith", P(T, ["John J. Smith"]), "John Jones", F, False),
    ("R3-1 Smith-Jones, J. vs John J. Smith", P(T, ["John J. Smith"]), "Smith-Jones, J.", F, False),
    (
        "R3-2 IL-6 at 6 hours",
        P("IL-8 effects at 6 hours", ["Ana Souza"]),
        "Souza",
        "IL-6 effects at 6 hours",
        False,
    ),
    ("R3-2 H2O vs H2S", P("H2S treatment", ["Ana Souza"]), "Souza", "H2O treatment", False),
    ("R3-2 H2O2 vs H2O", P("H2O toxicity", ["Ana Souza"]), "Souza", "H2O2 toxicity", False),
    ("R3-2 C2H6O vs C6H2O", P("C6H2O oxidation", ["Ana Souza"]), "Souza", "C2H6O oxidation", False),
    (
        "R3-2 BRCA1 single vs long title",
        P("BRCA1 mutations in breast cancer patients", ["Ana Souza"]),
        "Souza",
        "BRCA1",
        False,
    ),
    ("R3-3 WHO vs William Henry Oswald", P(T, ["William Henry Oswald"]), "WHO", F, False),
    (
        "R3-4 Phase I vs II",
        P("Phase II clinical trial of new cancer treatment", ["Ana Souza"]),
        "Souza",
        "Phase I clinical trial of new cancer treatment",
        False,
    ),
    (
        "R3-4 V vs X",
        P("Type X collagen in cartilage repair", ["Ana Souza"]),
        "Souza",
        "Type V collagen in cartilage repair",
        False,
    ),
    (
        "extra Vitamin D vs C",
        P("Vitamin C deficiency in older adults", ["Ana Souza"]),
        "Souza",
        "Vitamin D deficiency in older adults",
        False,
    ),
    # round 3 P2 false negatives
    ("R3-5 SMITH", P(T, ["John Smith"]), "SMITH", F, True),
    ("R3-5 LI", P(T, ["Wei Li"]), "LI", F, True),
    ("R3-5 RUMI", P(T, ["Rumi"]), "RUMI", F, True),
    ("R3-5 KIM, Min-Jun", P(T, ["Min-Jun Kim"]), "KIM, Min-Jun", F, True),
    ("R3-5 WHO vs WHO", P(T, ["WHO"]), "WHO", F, True),
    ("R3-5 WHO vs WHO alias", P(T, ["World Health Organization (WHO)"]), "WHO", F, True),
    ("R3-6 Smith, J. D. vs JD Smith", P(T, ["JD Smith"]), "Smith, J. D.", F, True),
    ("R3-6 Smith JD vs Smith JD", P(T, ["Smith JD"]), "Smith JD", F, True),
    ("R3-7 Kim M-J vs Min-Jun Kim", P(T, ["Min-Jun Kim"]), "Kim M-J", F, True),
    ("R3-7 J-D Smith vs John David Smith", P(T, ["John David Smith"]), "J-D Smith", F, True),
    ("R3-8 Kim, Min-Jun vs Minjun Kim", P(T, ["Minjun Kim"]), "Kim, Min-Jun", F, True),
    (
        "R3-9 em dash subtitle",
        P("What are the limits" + EM + "a systematic review", ["Ana Souza"]),
        "Souza",
        "What are the limits",
        True,
    ),
    # round 2
    (
        "R2 IL-6 long",
        P("Effects of IL-8 on human cell growth", ["Ana Souza"]),
        "Souza",
        "Effects of IL-6 on human cell growth",
        False,
    ),
    (
        "R2 AI vs AR long",
        P("AR methods for urban planning education in schools", ["Ana Souza"]),
        "Souza",
        "AI methods for urban planning education in schools",
        False,
    ),
    ("R2 BRCA single", P("BRCA123457", ["Ana Souza"]), "Souza", "BRCA123456", False),
    (
        "R2 CJK COVID 19 vs 18",
        P("城市交通预测COVID-18", ["王伟"]),
        "王伟",
        "城市交通预测COVID-19",
        False,
    ),
    ("R2 Smith JD", P(T, ["John David Smith"]), "Smith JD", F, True),
    ("R2 Wang Wei vs W. Wang", P(T, ["W. Wang"]), "Wang Wei", F, True),
    (
        "R2 WHO corporate",
        P(T, ["World Health Organization"]),
        "World Health Organization (WHO)",
        F,
        True,
    ),
    ("R2 WHO bare", P(T, ["World Health Organization"]), "WHO", F, True),
    ("R2 Camilo da Silva", P(T, ["Leonardo C. Silva"]), "Camilo da Silva, L.", F, True),
    (
        "R2 en dash",
        P("COVID" + EN + "19 pandemic", ["Ana Souza"]),
        "Souza",
        "COVID-19 pandemic",
        True,
    ),
    ("R2 space", P("COVID 19 pandemic", ["Ana Souza"]), "Souza", "COVID-19 pandemic", True),
    (
        "R2 COVID-19 vs 18",
        P("COVID-18 pandemic", ["Ana Souza"]),
        "Souza",
        "COVID-19 pandemic",
        False,
    ),
    ("R2 CJK short", P("城市", ["王伟"]), "王伟", "城市", True),
    (
        "R2 main title colon",
        P("What are the limits: a systematic review", ["Ana Souza"]),
        "Souza",
        "What are the limits",
        True,
    ),
    # round 1 + controls
    ("John Jones vs John Smith", P(T, ["John Smith"]), "John Jones", F, False),
    ("Jane Doe vs John Doe", P(T, ["John Doe"]), "Jane Doe", F, False),
    ("Li vs Wei Zhang", P(T, ["Wei Zhang"]), "Li", F, False),
    ("Li vs Wei Li", P(T, ["Wei Li"]), "Li", F, True),
    ("Wei Wang vs Wang Wei", P(T, ["Wang Wei"]), "Wei Wang", F, True),
    ("Smith J", P(T, ["John Smith"]), "Smith J", F, True),
    ("J. Smith", P(T, ["John Smith"]), "J. Smith", F, True),
    ("Smith, John vs Smith, J.", P(T, ["Smith, J."]), "Smith, John", F, True),
    ("John Smith vs John D. Smith", P(T, ["John D. Smith"]), "John Smith", F, True),
    ("Smith, J. vs Smith, John David", P(T, ["Smith, John David"]), "Smith, J.", F, True),
    ("da Silva, L.", P(T, ["Leonardo Camilo da Silva"]), "da Silva, L.", F, True),
    ("van der Berg", P(T, ["Pieter van der Berg"]), "van der Berg, P.", F, True),
    ("de la Fuente", P(T, ["María de la Fuente"]), "de la Fuente, M.", F, True),
    ("Smith-Jones full", P(T, ["Mary Smith-Jones"]), "Smith-Jones, M.", F, True),
    ("Silva; Souza second author", P(T, ["Ana Souza", "Leo Silva"]), "Silva; Souza", F, True),
    ("Mergel et al.", P(T, ["Ines Mergel", "N Edelmann"]), "Mergel et al.", F, True),
    ("accents", P(T, ["Maria Gonçalves"]), "Goncalves, M.", F, True),
    ("japanese", P(T, ["Takeshi Yamamoto"]), "Yamamoto, T.", F, True),
    ("CJK different", P("蛋白质结构", ["王伟"]), "王伟", "城市交通", False),
    ("CJK fragment", P("城市交通预测研究", ["王伟"]), "王伟", "城市交通", True),
    (
        "IL6 vs IL-6",
        P("Effects of IL6 on cell growth", ["Ana Souza"]),
        "Souza",
        "Effects of IL-6 on cell growth",
        True,
    ),
    ("acronyms only", P("AI ML NLP", ["Ana Souza"]), "Souza", "AI ML NLP", True),
    (
        "typo",
        P("Digital transformation in the public sector", ["Ines Mergel"]),
        "Mergel",
        "digital transformaton public sectors",
        True,
    ),
    ("stopwords only", P("The", ["Ana Souza"]), "Souza", "the of", False),
    (
        "single word not title",
        P("Digital transformation in the public sector", ["Ines Mergel"]),
        "Mergel",
        "digital",
        False,
    ),
    ("Leviathan", P("Leviathan", ["Thomas Hobbes"], 1651), "Hobbes", "Leviathan", True),
    (
        "year range in title",
        P("Urban growth 1990" + EN + "2020 in Brazil", ["Ana Souza"]),
        "Souza",
        "urban growth 1990-2020 brazil",
        True,
    ),
    (
        "Phase 2 vs Phase II",
        P("A Phase 2 trial of drug X", ["Ana Souza"]),
        "Souza",
        "Phase 2 trial drug X",
        True,
    ),
    (
        "real Mergel paper",
        P(
            "Defining digital transformation: Results from expert interviews",
            ["Ines Mergel", "Noella Edelmann", "Nathalie Haug"],
            2019,
        ),
        "Mergel",
        "defining digital transformation expert interviews",
        True,
    ),
]


@pytest.mark.parametrize(
    ("paper", "cited", "fragment", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_case(paper: Paper, cited: str, fragment: str, expected: bool) -> None:
    m = score_candidate(paper, cited, paper.year, fragment)
    assert m.verified is expected, (m.checks(), m.reasons())
