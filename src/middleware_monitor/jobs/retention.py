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
    LoginAttempt,
    SystemLog,
    WebhookEvent,
)
from middleware_monitor.domain.config.repository import load_config

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

        a = _delete_count(db, delete(DevicePing).where(DevicePing.timestamp < cutoff_pings))
        b = _delete_count(db, delete(WebhookEvent).where(WebhookEvent.timestamp < cutoff_webhooks))
        c = _delete_count(db, delete(Collection).where(Collection.collected_at < cutoff_collections))
        d = _delete_count(db, delete(SystemLog).where(SystemLog.timestamp < cutoff_syslogs))
        e = _delete_count(db, delete(LoginAttempt).where(LoginAttempt.timestamp < cutoff_login))
        db.commit()

    log.info(
        "retention_ok",
        device_pings=a,
        webhook_events=b,
        collections=c,
        system_logs=d,
        login_attempts=e,
    )
