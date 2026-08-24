"""Job: backup automatico diario.

Grava **sempre** o snapshot do banco e, quando existe passphrase salva, tambem
o pacote portavel de configuracao. Depois aplica a poda (quantidade + espaco).

O trabalho pesado roda em thread: um ``VACUUM INTO`` de um banco com ledger
MQTT leva segundos, e o scheduler compartilha o event loop com a API e o
coletor — bloquear aqui pararia a ingestao.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.export_crypto import encrypt_export
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.scheduler import add_cron_job, remove_job, reschedule_cron
from middleware_monitor.domain.backup import bundle as bundle_mod
from middleware_monitor.domain.backup import snapshot as snap
from middleware_monitor.domain.backup.settings import (
    BackupSettings,
    load_backup_settings,
    load_export_passphrase,
    record_run,
)

log = get_logger("backup")

BACKUP_JOB_ID = "backup_daily"


def _local_timezone() -> Any:
    tz = datetime.now().astimezone().tzinfo
    return tz or UTC


def _write_bundle(passphrase: str) -> Path:
    with session_factory() as db:
        data = bundle_mod.build(db)
    blob = encrypt_export(bundle_mod.to_bytes(data), passphrase)
    destino = snap.backups_dir() / f"config-{datetime.now():%Y%m%d-%H%M%S}-auto.mwrbak"
    destino.write_bytes(blob)
    return destino


def _run_sync() -> dict[str, Any]:
    with session_factory() as db:
        cfg = load_backup_settings(db)
        passphrase = load_export_passphrase(db)
    arquivo = snap.create_snapshot(label="auto")
    resultado: dict[str, Any] = {"snapshot": arquivo.name, "bundle": ""}
    if passphrase:
        resultado["bundle"] = _write_bundle(passphrase).name
    resultado["pruned"] = snap.prune(keep=cfg.keep, max_bytes=cfg.max_mb * 1024 * 1024)
    return resultado


async def run_backup() -> None:
    try:
        resultado = await asyncio.to_thread(_run_sync)
    except Exception as exc:
        log.error("backup_failed", error=type(exc).__name__, message=str(exc))
        with session_factory() as db:
            record_run(db, status="erro", detail=f"{type(exc).__name__}: {exc}")
        return
    detalhe = resultado["snapshot"]
    if resultado["bundle"]:
        detalhe += f" + {resultado['bundle']}"
    with session_factory() as db:
        record_run(db, status="ok", detail=detalhe)
    log.info(
        "backup_ok",
        snapshot=resultado["snapshot"],
        bundle=resultado["bundle"] or None,
        pruned=len(resultado["pruned"]),
    )


def apply_backup_schedule(cfg: BackupSettings) -> None:
    """(Re)agenda o backup diario conforme a config, ou o remove.

    Chamado no boot e sempre que a tela salva, para mudar horario valer na hora.
    O horario e o do relogio do servidor (o scheduler roda em UTC).
    """
    if not cfg.auto_enabled:
        remove_job(BACKUP_JOB_ID)
        log.info("backup_auto_disabled")
        return
    tz = _local_timezone()
    if not reschedule_cron(
        BACKUP_JOB_ID, hour=cfg.hour, minute=cfg.minute, timezone=tz,
    ):
        add_cron_job(
            run_backup,
            job_id=BACKUP_JOB_ID,
            hour=cfg.hour,
            minute=cfg.minute,
            timezone=tz,
        )
    log.info("backup_scheduled", at=f"{cfg.hour:02d}:{cfg.minute:02d}", keep=cfg.keep)
