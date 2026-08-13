"""CLI administrativo — migrações, espelho de integridade, manutenção.

Existe para que os timers do systemd e o deploy não precisem de um script solto
fora do repositório, que foi como o v1 acabou com o refresh de rankings vivendo
em ``/root/bxat/deploy/scripts/refresh_scholar_rankings.sh`` e os 17 MB de dados
untracked em ``packages/bx-scholar-core/data/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from bx_scholar_core.logging import setup_logging

from bx_scholar.config import load_settings
from bx_scholar.store import db


async def _cmd_health(_args) -> int:
    settings = load_settings()
    db.init_engine(settings.database_url)
    health = await db.healthcheck()

    from bx_scholar.workflows.integrity import mirror_is_populated

    populated = await mirror_is_populated()
    print(json.dumps({**health, "integrity_mirror_populated": populated}, indent=2))
    await db.dispose()
    return 0


async def _cmd_refresh_integrity(args) -> int:
    from bx_scholar.harvest import retraction_watch

    settings = load_settings()
    db.init_engine(settings.database_url)
    try:
        report = await retraction_watch.refresh(settings.polite_email)
    except Exception as exc:
        print(f"[ERRO] refresh do espelho de integridade falhou: {exc}", file=sys.stderr)
        await db.dispose()
        # Código != 0 para o systemd marcar a unidade como falha e o
        # OnFailure/journal registrar. Silenciar aqui deixaria o espelho
        # envelhecendo sem ninguém notar.
        return 1

    print(
        json.dumps(
            {
                "fetched_rows": report.fetched_rows,
                "stored": report.stored,
                "by_status": report.by_status,
                "skipped_no_doi": report.skipped_no_doi,
                "elapsed_s": round(report.elapsed_s, 1),
            },
            indent=2,
        )
    )
    await db.dispose()
    return 0


async def _cmd_cache_sweep(_args) -> int:
    from bx_scholar.cache.local import LocalHTTPCache

    settings = load_settings()
    cache = LocalHTTPCache(settings.cache_dir / "http", enabled=True)
    print(json.dumps({**await cache.sweep(), **await cache.stats()}, indent=2))
    return 0


_COMMANDS = {
    "health": _cmd_health,
    "refresh-integrity": _cmd_refresh_integrity,
    "cache-sweep": _cmd_cache_sweep,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="bx-scholar-admin", description=__doc__)
    parser.add_argument("command", choices=sorted(_COMMANDS))
    args = parser.parse_args()

    settings_level = "INFO"
    setup_logging(level=settings_level, fmt="console")
    raise SystemExit(asyncio.run(_COMMANDS[args.command](args)))


if __name__ == "__main__":
    main()
