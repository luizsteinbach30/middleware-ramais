"""Job: enforce retention policies on log/event tables."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Delete, delete
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.db import get_engine, session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import (
    Collection,
    DevicePing,
    ExtensionCall,
    ExtensionDailyStat,
    LoginAttempt,
    SystemLog,
    WebhookEvent,
)
from middleware_monitor.domain.config.repository import load_config
from middleware_monitor.domain.mqtt import realtime as mqtt_realtime
from middleware_monitor.domain.mqtt import repository as mqtt_repo
from middleware_monitor.domain.mqtt import storage as mqtt_storage

log = get_logger("retention")

# Quando compactar. O SQLite não devolve ao sistema de arquivos a página que a
# poda esvaziou: ela vira freelist e o arquivo só cresce. Medido em 2026-08-24
# no banco do cliente (docs/design/PERF_BASELINE.md): **44% do arquivo** — 24,4
# das 55,4 MB — era freelist, e um `VACUUM` de 240 ms devolveu tudo.
#
# Não roda todo dia de propósito. O `VACUUM` reescreve o arquivo inteiro, segura
# escrita enquanto isso e exige espaço em disco equivalente ao banco; num dia em
# que a poda apagou pouco, o ganho não paga a reescrita. Os dois limiares se
# complementam: a fração evita reescrever por migalha num banco grande, e o piso
# absoluto evita reescrever um banco pequeno por causa de uma fração alta.
_VACUUM_FRACAO_MINIMA = 0.20
_VACUUM_BYTES_MINIMOS = 8 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _compactar() -> dict[str, Any]:
    """Recupera o espaço livre do banco, quando há o suficiente para valer.

    Devolve sempre o diagnóstico (livre antes, decisão, e o que foi recuperado),
    para o log da poda dizer por que compactou ou por que não.
    """
    engine = get_engine()
    if engine.dialect.name != "sqlite":
        return {"compactou": False, "motivo": "nao_sqlite"}
    try:
        # AUTOCOMMIT porque `VACUUM` não roda dentro de transação.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            page_size = int(conn.exec_driver_sql("PRAGMA page_size").scalar() or 0)
            paginas = int(conn.exec_driver_sql("PRAGMA page_count").scalar() or 0)
            livres = int(conn.exec_driver_sql("PRAGMA freelist_count").scalar() or 0)
            if not page_size or not paginas:
                return {"compactou": False, "motivo": "sem_pragma"}

            livre_bytes = livres * page_size
            fracao = livres / paginas
            if livre_bytes < _VACUUM_BYTES_MINIMOS or fracao < _VACUUM_FRACAO_MINIMA:
                return {
                    "compactou": False,
                    "motivo": "abaixo_do_limiar",
                    "livre_mb": round(livre_bytes / 1024 / 1024, 1),
                    "livre_pct": round(100 * fracao),
                }

            inicio = time.perf_counter()
            conn.exec_driver_sql("VACUUM")
            duracao_ms = int((time.perf_counter() - inicio) * 1000)
            depois = int(conn.exec_driver_sql("PRAGMA page_count").scalar() or 0)
            return {
                "compactou": True,
                "liberado_mb": round((paginas - depois) * page_size / 1024 / 1024, 1),
                "antes_mb": round(paginas * page_size / 1024 / 1024, 1),
                "depois_mb": round(depois * page_size / 1024 / 1024, 1),
                "duracao_ms": duracao_ms,
            }
    except Exception as exc:  # pragma: no cover - disco cheio, lock, etc.
        # Falhar aqui não pode derrubar a poda: as linhas já foram apagadas, e
        # o espaço não recuperado é problema de espaço, não de correção.
        log.warning("vacuum_falhou", error=type(exc).__name__, message=str(exc))
        return {"compactou": False, "motivo": "erro"}


def _delete_count(db: DBSession, stmt: Delete) -> int:
    """Executa um DELETE e devolve o nº de linhas afetadas.

    ``Session.execute`` é tipado como ``Result``; em runtime é um
    ``CursorResult`` que expõe ``rowcount``. Cast restrito ao tipo real.
    """
    return cast("CursorResult[Any]", db.execute(stmt)).rowcount


async def run_retention() -> None:
    with session_factory() as db:
        cfg = load_config(db)
        cutoff_pings = _now() - timedelta(days=cfg.device_ping_retention_days)
        cutoff_webhooks = _now() - timedelta(days=cfg.webhook_log_retention_days)
        cutoff_collections = _now() - timedelta(days=cfg.collection_retention_days)
        cutoff_syslogs = _now() - timedelta(days=cfg.system_log_retention_days)
        cutoff_login = _now() - timedelta(days=14)
        cutoff_mqtt = _now() - timedelta(days=cfg.mqtt_message_retention_days)
        # A prova de cobertura e minuscula em disco e so vale acompanhada de um
        # historico longo: guarda-se por 1 ano, independente do ledger.
        cutoff_mqtt_conn = _now() - timedelta(days=365)
        cutoff_ext_events = _now() - timedelta(days=cfg.extension_event_retention_days)
        cutoff_chamadas = _now() - timedelta(days=cfg.extension_call_retention_days)
        # O resumo diario e comparado por texto (AAAA-MM-DD), que ordena igual a
        # data — e o que permite podar sem converter linha a linha.
        corte_resumo = (
            _now() - timedelta(days=cfg.extension_daily_stats_retention_days)
        ).date().isoformat()

        a = _delete_count(db, delete(DevicePing).where(DevicePing.timestamp < cutoff_pings))
        b = _delete_count(db, delete(WebhookEvent).where(WebhookEvent.timestamp < cutoff_webhooks))
        c = _delete_count(db, delete(Collection).where(Collection.collected_at < cutoff_collections))
        d = _delete_count(db, delete(SystemLog).where(SystemLog.timestamp < cutoff_syslogs))
        e = _delete_count(db, delete(LoginAttempt).where(LoginAttempt.timestamp < cutoff_login))

        # Ledger MQTT: por idade e, se houver teto, por volume de payload.
        # Mensagens fixadas como evidencia nunca sao apagadas (ver repository).
        f = mqtt_repo.purge_messages_by_age(db, cutoff_mqtt)
        g = mqtt_repo.purge_messages_by_size(db, cfg.mqtt_message_max_mb * 1024 * 1024)
        h = mqtt_repo.purge_connection_events(db, cutoff_mqtt_conn)
        i = mqtt_realtime.purge_status_events(db, cutoff_ext_events)
        j = _delete_count(db, delete(ExtensionCall).where(ExtensionCall.started_at < cutoff_chamadas))
        k = _delete_count(db, delete(ExtensionDailyStat).where(ExtensionDailyStat.dia < corte_resumo))
        db.commit()

    # A poda muda de uma vez os dois números que a tela de config mostra.
    mqtt_storage.invalidate()

    log.info(
        "retention_ok",
        device_pings=a,
        webhook_events=b,
        collections=c,
        system_logs=d,
        login_attempts=e,
        mqtt_messages=f + g,
        mqtt_connection_events=h,
        extension_status_events=i,
        extension_calls=j,
        extension_daily_stats=k,
    )

    # Depois de apagar, e não antes: o espaço a recuperar é justamente o que a
    # poda acabou de liberar.
    log.info("retention_vacuum", **_compactar())
