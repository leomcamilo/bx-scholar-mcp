"""Schema do store durável — dialeto-agnóstico por construção.

Regra que vale para todo este módulo: **nada específico de um dialeto no
núcleo**. JSON entra por ``JSONCol`` (que vira ``JSONB`` no Postgres e ``JSON``
no resto), datas são epoch em ``BigInteger``, e não há FTS5 nem ``tsvector``
aqui. O casamento fuzzy de nome de veículo está em ``store/venues.py`` e é
100% portátil (``rapidfuzz`` em Python) — o caminho rápido com ``pg_trgm`` foi
planejado e NÃO existe; ver a nota de custo em ``venues.lookup``. Trocar de
engine é mudar ``BX_SCHOLAR_DATABASE_URL``.

Separação deliberada (ver o plano): o **cache HTTP não mora aqui**. São dezenas
de MB de escrita de alta rotação a cada busca — num Postgres isso vira pressão
de autovacuum e round-trip de rede para algo puramente descartável. Cache é
arquivo local sob ``CACHE_DIR``; este banco guarda só o que precisa sobreviver.
"""

from __future__ import annotations

import time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB no Postgres (indexável, com operadores), JSON portátil no resto.
JSONCol = JSON().with_variant(postgresql.JSONB(), "postgresql")


def now_ts() -> int:
    return int(time.time())


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Entity store — obras e proveniência
# --------------------------------------------------------------------------


class Work(Base):
    """Registro consolidado de uma obra, chaveado por identificador canônico.

    ``work_key`` é a identidade estável do v2: ``doi:10.1234/abc``,
    ``pmid:12345678``, ``openalex:W123``, ou ``title:<sha1-12>`` quando não há
    identificador nenhum. O v1 tinha DUAS noções de identidade concorrentes
    (``dedup.py:10`` e ``tools/snowball.py:43``); aqui existe uma só.
    """

    __tablename__ = "work"

    work_key: Mapped[str] = mapped_column(String(128), primary_key=True)

    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    pmid: Mapped[str | None] = mapped_column(String(32), index=True)
    pmcid: Mapped[str | None] = mapped_column(String(32), index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), index=True)
    openalex_id: Mapped[str | None] = mapped_column(String(32), index=True)
    s2_id: Mapped[str | None] = mapped_column(String(64), index=True)
    scielo_pid: Mapped[str | None] = mapped_column(String(64), index=True)

    title: Mapped[str | None] = mapped_column(Text)
    title_norm: Mapped[str | None] = mapped_column(String(512), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    work_type: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(16))

    venue_name: Mapped[str | None] = mapped_column(Text)
    issn_l: Mapped[str | None] = mapped_column(String(16), index=True)

    cited_by_count: Mapped[int | None] = mapped_column(Integer)
    is_open_access: Mapped[bool | None] = mapped_column(Boolean)
    oa_status: Mapped[str | None] = mapped_column(String(32))

    # Estado de integridade materializado (denormalizado de `integrity` para
    # que o gate não precise de join no caminho quente da busca).
    integrity_status: Mapped[str] = mapped_column(String(24), default="unknown")

    # Dict consolidado do Paper (autores, abstract, urls...). Campos escalares
    # acima existem para filtro/índice; este é o registro completo.
    payload: Mapped[dict] = mapped_column(JSONCol, default=dict)

    first_seen_at: Mapped[int] = mapped_column(BigInteger, default=now_ts)
    last_seen_at: Mapped[int] = mapped_column(BigInteger, default=now_ts)


class WorkSource(Base):
    """Proveniência por fonte — o que o v1 não tinha.

    No v1 havia só ``Paper.source_api``, um campo único que registrava a API que
    produziu o registro inteiro — e que era **perdido no merge do dedup**
    (``dedup.py:42-57`` mantinha o registro de maior ``_metadata_score`` e
    descartava o outro). Sem timestamp de recuperação, sem URL do registro.

    Aqui cada fonte que viu a obra deixa sua própria linha, com quando e onde.
    """

    __tablename__ = "work_source"
    __table_args__ = (
        UniqueConstraint("work_key", "source", name="uq_work_source"),
        Index("ix_work_source_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_key: Mapped[str] = mapped_column(String(128), ForeignKey("work.work_key"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(255))
    record_url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[int] = mapped_column(BigInteger, default=now_ts)
    raw: Mapped[dict] = mapped_column(JSONCol, default=dict)


# --------------------------------------------------------------------------
# Integridade — o gate roda ANTES da seleção
# --------------------------------------------------------------------------


class Integrity(Base):
    """Espelho local do Retraction Watch (CC0 via Crossref) + sinais do CrossRef.

    Manter espelho local em vez de consultar por artigo é o que torna o gate
    viável no caminho quente: são ~60k linhas, atualizadas por timer diário.

    ``status`` segue a política do plano: ``clear``, ``retracted``, ``concern``
    (expressão de preocupação), ``corrected``, ``reinstated``, ``unknown``.
    Nunca inferir ``clear`` por ausência — ausência é ``unknown``.
    """

    __tablename__ = "integrity"

    doi: Mapped[str] = mapped_column(String(255), primary_key=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    nature: Mapped[str | None] = mapped_column(String(64))
    notice_doi: Mapped[str | None] = mapped_column(String(255))
    notice_date: Mapped[str | None] = mapped_column(String(32))
    reasons: Mapped[list] = mapped_column(JSONCol, default=list)
    source: Mapped[str] = mapped_column(String(32), default="retraction_watch")
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ts)


# --------------------------------------------------------------------------
# Veículos — SJR / Qualis / JQL saem de CSV+XLSX em memória para tabela
# --------------------------------------------------------------------------


class Venue(Base):
    """Veículo com seus indicadores, guardados COM contexto.

    Qualis é sempre (área, quadriênio, estrato) e SJR é sempre (ano, categoria,
    quartil) — nunca um número solto. Um veículo bem avaliado numa área pode ser
    irrelevante em outra, e nada disso é garantia sobre um artigo individual.

    Motivação operacional: hoje ``rankings/service.py`` carrega 53.404 linhas de
    SJR, 33.339 de Qualis e 842 de JQL como dicts em memória no startup, a partir
    de 17 MB em ``packages/bx-scholar-core/data/`` que estão **untracked no git**.
    """

    __tablename__ = "venue"
    __table_args__ = (
        Index("ix_venue_name_norm", "name_norm"),
        Index("ix_venue_issn_l", "issn_l"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issn_l: Mapped[str | None] = mapped_column(String(16))
    issns: Mapped[list] = mapped_column(JSONCol, default=list)
    name: Mapped[str] = mapped_column(Text)
    name_norm: Mapped[str] = mapped_column(String(512))
    publisher: Mapped[str | None] = mapped_column(Text)

    # [{"year": 2024, "categoria": "...", "quartil": "Q1", "sjr": 1.23}, ...]
    sjr: Mapped[list] = mapped_column(JSONCol, default=list)
    # [{"area": "ADMINISTRAÇÃO...", "quadrienio": "2017-2020", "estrato": "A2"}, ...]
    qualis: Mapped[list] = mapped_column(JSONCol, default=list)
    # [{"sistema": "ABS", "nota": "4"}, {"sistema": "ABDC", "nota": "A"}, ...]
    jql: Mapped[list] = mapped_column(JSONCol, default=list)

    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ts)


# --------------------------------------------------------------------------
# Evidence Packs — o artefato persistido
# --------------------------------------------------------------------------


class Pack(Base):
    """Evidence Pack: unidade persistente, versionada, navegável e exportável.

    O modelo NUNCA recebe isto inteiro — recebe a projeção montada em
    ``projection.py``, com teto de caracteres, e usa ``read_pack`` para
    aprofundar. Essa é a diferença entre um artefato e um payload de prompt.
    """

    __tablename__ = "pack"

    pack_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # search | verification
    mode: Mapped[str | None] = mapped_column(String(16))  # quick | balanced | deep
    status: Mapped[str] = mapped_column(String(16), default="building", index=True)

    query: Mapped[dict] = mapped_column(JSONCol, default=dict)
    counts: Mapped[dict] = mapped_column(JSONCol, default=dict)
    coverage: Mapped[dict] = mapped_column(JSONCol, default=dict)
    limitations: Mapped[list] = mapped_column(JSONCol, default=list)
    provenance: Mapped[dict] = mapped_column(JSONCol, default=dict)

    workflow_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ts, index=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ts)


class PackItem(Base):
    """Item de um pack: obra selecionada, claim, span de evidência, contradição."""

    __tablename__ = "pack_item"
    __table_args__ = (
        Index("ix_pack_item_lookup", "pack_id", "item_type", "rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pack_id: Mapped[str] = mapped_column(String(40), ForeignKey("pack.pack_id"), index=True)
    item_type: Mapped[str] = mapped_column(String(24))
    ref_key: Mapped[str | None] = mapped_column(String(128), index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    # Obras retratadas ficam no pack mas fora da seleção — o bloqueio é para
    # usá-las como sustentação não sinalizada, não para sumir com elas.
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    payload: Mapped[dict] = mapped_column(JSONCol, default=dict)


# --------------------------------------------------------------------------
# Texto integral — documento resolvido e trechos localizáveis
# --------------------------------------------------------------------------


class FulltextDoc(Base):
    """Documento resolvido. ``retrieve_fulltext`` devolve handle + trechos,
    nunca o texto inteiro — o texto vive aqui e no cache local."""

    __tablename__ = "fulltext_doc"

    doc_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    work_key: Mapped[str] = mapped_column(String(128), index=True)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)

    # open_fulltext | abstract_only | closed | user_provided
    access_status: Mapped[str] = mapped_column(String(32))
    license: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(32))
    parser: Mapped[str | None] = mapped_column(String(32))

    char_count: Mapped[int | None] = mapped_column(Integer)
    sections: Mapped[list] = mapped_column(JSONCol, default=list)
    stored_path: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[int] = mapped_column(BigInteger, default=now_ts)


class FulltextSpan(Base):
    """Trecho com localização. É isto que sustenta ``verification_basis`` —
    sem span localizável, a base cai para ``abstract`` ou ``metadata_only``."""

    __tablename__ = "fulltext_span"
    __table_args__ = (Index("ix_span_doc_ordinal", "doc_id", "ordinal"),)

    span_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(40), ForeignKey("fulltext_doc.doc_id"), index=True)
    section: Mapped[str | None] = mapped_column(String(64))
    section_title: Mapped[str | None] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)


# --------------------------------------------------------------------------
# Jobs — modo deep
# --------------------------------------------------------------------------


class Job(Base):
    """Execução assíncrona do modo deep.

    O BXat tem teto de 4 chamadas MCP por turno (``BXAT_AGENT_MCP_TURN_BUDGET``),
    então o polling acontece no turno seguinte, não em laço dentro do mesmo.
    """

    __tablename__ = "job"

    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    pack_id: Mapped[str | None] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(16), default="deep")
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[dict] = mapped_column(JSONCol, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ts)
    started_at: Mapped[int | None] = mapped_column(BigInteger)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)


# --------------------------------------------------------------------------
# Fosso brasileiro — índice local BDTD / CAPES
# --------------------------------------------------------------------------


class BrRecord(Base):
    """Teses, dissertações e produção brasileira colhidas em lote.

    Índice local em vez de consulta remota no caminho quente: BDTD é OAI-PMH e o
    Catálogo CAPES é dataset em lote — nenhum dos dois responde em tempo de chat.
    """

    __tablename__ = "br_record"
    __table_args__ = (
        Index("ix_br_source_year", "source", "year"),
        Index("ix_br_title_norm", "title_norm"),
    )

    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(16))  # bdtd | capes
    title: Mapped[str] = mapped_column(Text)
    title_norm: Mapped[str] = mapped_column(String(512))
    authors: Mapped[list] = mapped_column(JSONCol, default=list)
    advisors: Mapped[list] = mapped_column(JSONCol, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    institution: Mapped[str | None] = mapped_column(Text)
    program: Mapped[str | None] = mapped_column(Text)
    knowledge_area: Mapped[str | None] = mapped_column(Text)
    degree: Mapped[str | None] = mapped_column(String(32))  # mestrado | doutorado
    language: Mapped[str | None] = mapped_column(String(16))
    abstract: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    payload: Mapped[dict] = mapped_column(JSONCol, default=dict)
    harvested_at: Mapped[int] = mapped_column(BigInteger, default=now_ts)


class HarvestState(Base):
    """Marca d'água por harvester — retomada incremental do OAI-PMH e dos
    datasets CAPES sem recolher tudo de novo a cada timer."""

    __tablename__ = "harvest_state"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[int | None] = mapped_column(BigInteger)
    last_ok_at: Mapped[int | None] = mapped_column(BigInteger)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict] = mapped_column(JSONCol, default=dict)
