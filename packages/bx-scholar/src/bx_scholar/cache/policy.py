"""Política de cache — um lugar só.

No v1 o TTL era um literal repetido em ~20 pontos de chamada
(``cache_policy=("search_results", 3600)`` espalhado por ``clients/openalex.py``,
``crossref.py``, ``arxiv.py``, ``scielo.py``, ``semantic_scholar.py``,
``unpaywall.py``, ``tavily.py`` e ``tools/``). Mudar a validade de um tipo de
entidade exigia caçar todas as ocorrências, e divergências passavam sem ruído.

Aqui o TTL é função do tipo de entidade, e o tipo de entidade é um enum fechado.
"""

from __future__ import annotations

from enum import StrEnum

DAY = 86_400


class Entity(StrEnum):
    """Tipos de entidade cacheáveis."""

    SEARCH_RESULTS = "search_results"
    WORK_METADATA = "work_metadata"
    CITATIONS = "citations"
    AUTHOR = "author"
    VENUE_INFO = "venue_info"
    OA_STATUS = "oa_status"
    FULLTEXT = "fulltext"
    INTEGRITY = "integrity"
    TRENDS = "trends"


# Racional dos prazos, não números arbitrários:
#
# - busca envelhece rápido porque o ranking upstream muda e o usuário espera
#   novidade; 1h é o suficiente para absorver a repetição dentro de uma sessão;
# - metadado de obra é praticamente imutável depois de publicado — 7 dias;
# - contagem de citação muda devagar, mas muda — 1 dia;
# - texto integral de obra publicada não muda: 30 dias, e a chave inclui o
#   hash do documento, então uma versão nova é outra entrada;
# - integridade tem TTL curto DE PROPÓSITO. Uma retratação recém-publicada não
#   pode ficar 7 dias invisível atrás de cache — o custo de errar aqui é citar
#   um artigo retratado como se fosse válido. O espelho local diário é a fonte
#   primária; este TTL vale só para consulta pontual ao CrossRef.
_TTL: dict[Entity, int] = {
    Entity.SEARCH_RESULTS: 3_600,
    Entity.WORK_METADATA: 7 * DAY,
    Entity.CITATIONS: 1 * DAY,
    Entity.AUTHOR: 7 * DAY,
    Entity.VENUE_INFO: 30 * DAY,
    Entity.OA_STATUS: 7 * DAY,
    Entity.FULLTEXT: 30 * DAY,
    Entity.INTEGRITY: 6 * 3_600,
    Entity.TRENDS: 1 * DAY,
}


def ttl(entity: Entity) -> int:
    return _TTL[entity]


def policy(entity: Entity) -> tuple[str, int]:
    """Tupla ``(entity_type, ttl)`` no formato que ``AsyncHTTPClient`` espera."""
    return (str(entity), _TTL[entity])
