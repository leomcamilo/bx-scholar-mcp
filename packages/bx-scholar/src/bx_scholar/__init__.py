"""BX-Scholar v2 — evidence-oriented academic research.

Arquitetura em quatro camadas (ver docs/adr/0001-quatro-camadas.md):

1. Conectores      — detalhe de infraestrutura, nunca exposto ao agente.
2. Operações       — as poucas tools públicas: decisões que cabem ao modelo.
3. Determinístico  — normalização, dedup, integridade, indicadores. Sem LLM.
4. Evidence Pack   — artefato persistido; o modelo recebe projeções pequenas.
"""

__version__ = "2.0.0.dev0"

WORKFLOW_VERSION = "bx-scholar-v2.0"
