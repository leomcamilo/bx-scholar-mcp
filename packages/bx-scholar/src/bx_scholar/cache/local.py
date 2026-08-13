"""Cache HTTP local em arquivo — efêmero e descartável, fora do banco.

Por que não fica no store durável (a decisão está no plano): são dezenas de MB
de escrita de alta rotação a cada busca. Num Postgres isso vira pressão de
autovacuum e round-trip de rede para algo que é pura otimização local. Num
DuckDB — o que o v1 fazia — vira lock exclusivo de arquivo, e foi exatamente
isso que produziu DOIS bancos de ~90 MB em disco cacheando o mesmo dado
upstream, um por processo, porque dois processos não conseguem compartilhar.

Arquivo simples resolve os dois problemas: qualquer número de processos lê e
escreve em paralelo, e apagar o diretório é uma operação segura a qualquer
momento. O formato é 8 bytes de expiração (epoch, big-endian) + corpo.

Implementa o mesmo contrato mínimo de ``bx_scholar_core.cache.store.CacheStore``
(``get``/``put``), então ``AsyncHTTPClient`` funciona sem alteração.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
import time
from pathlib import Path
from typing import Any

from bx_scholar_core.logging import get_logger

logger = get_logger(__name__)

_HEADER = struct.Struct(">Q")


def make_cache_key(url: str, params: dict[str, Any] | None = None) -> str:
    payload = f"{url}:{json.dumps(params or {}, sort_keys=True, default=str)}"
    return hashlib.sha256(payload.encode()).hexdigest()


class LocalHTTPCache:
    """Cache de respostas HTTP em disco, com TTL por entrada.

    Thread/task-safe por construção: escrita vai para arquivo temporário e é
    renomeada (``os.replace`` é atômico no mesmo filesystem), então um leitor
    nunca enxerga entrada pela metade.
    """

    def __init__(self, root: Path, *, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Dois níveis de shard: um diretório com centenas de milhares de
        # arquivos degrada listagem e alguns filesystems reclamam.
        return self.root / key[:2] / key[2:4] / f"{key}.bin"

    async def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        return await asyncio.to_thread(self._get_sync, key)

    def _get_sync(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            raw = path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            logger.debug("cache_read_failed", key=key, error=str(exc))
            return None

        if len(raw) < _HEADER.size:
            path.unlink(missing_ok=True)
            return None

        (expires_at,) = _HEADER.unpack(raw[: _HEADER.size])
        if expires_at <= time.time():
            # Expiração é preguiçosa e efetiva: quem lê uma entrada vencida a
            # remove. O v1 tinha evict_expired() e nunca o chamava, então linhas
            # vencidas se acumulavam para sempre.
            path.unlink(missing_ok=True)
            return None

        return raw[_HEADER.size :]

    async def put(self, key: str, entity_type: str, content: bytes, ttl: int) -> None:
        if not self.enabled:
            return
        await asyncio.to_thread(self._put_sync, key, content, ttl)

    def _put_sync(self, key: str, content: bytes, ttl: int) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(_HEADER.pack(int(time.time()) + int(ttl)) + content)
            tmp.replace(path)
        except OSError as exc:
            # Cache é otimização — nunca pode derrubar a requisição.
            logger.debug("cache_write_failed", key=key, error=str(exc))

    async def sweep(self) -> dict[str, int]:
        """Remove entradas vencidas. Chamado no startup e por timer."""
        return await asyncio.to_thread(self._sweep_sync)

    def _sweep_sync(self) -> dict[str, int]:
        removed = kept = 0
        now = time.time()
        for path in self.root.rglob("*.bin"):
            try:
                with path.open("rb") as fh:
                    head = fh.read(_HEADER.size)
                if len(head) < _HEADER.size or _HEADER.unpack(head)[0] <= now:
                    path.unlink(missing_ok=True)
                    removed += 1
                else:
                    kept += 1
            except OSError:
                continue
        logger.info("cache_swept", removed=removed, kept=kept)
        return {"removed": removed, "kept": kept}

    async def stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._stats_sync)

    def _stats_sync(self) -> dict[str, Any]:
        entries = 0
        total = 0
        for path in self.root.rglob("*.bin"):
            try:
                total += path.stat().st_size
                entries += 1
            except OSError:
                continue
        return {"entries": entries, "bytes": total, "root": str(self.root)}
