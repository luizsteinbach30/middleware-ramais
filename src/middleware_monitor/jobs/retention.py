"""Job: enforce retention policies on log/event tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Delete, delete
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.db import session_factory
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

log = get_logger("retention")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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
