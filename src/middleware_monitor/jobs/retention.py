"""Job: enforce retention policies on log/event tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

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


async def run_retention() -> None:
    with session_factory() as db:
        cfg = load_config(db)
        cutoff_pings = _now() - timedelta(days=cfg.device_ping_retention_days)
        cutoff_webhooks = _now() - timedelta(days=cfg.webhook_log_retention_days)
        cutoff_collections = _now() - timedelta(days=cfg.collection_retention_days)
        cutoff_syslogs = _now() - timedelta(days=cfg.system_log_retention_days)
        cutoff_login = _now() - timedelta(days=14)

        a = db.execute(delete(DevicePing).where(DevicePing.timestamp < cutoff_pings)).rowcount
        b = db.execute(delete(WebhookEvent).where(WebhookEvent.timestamp < cutoff_webhooks)).rowcount
        c = db.execute(delete(Collection).where(Collection.collected_at < cutoff_collections)).rowcount
        d = db.execute(delete(SystemLog).where(SystemLog.timestamp < cutoff_syslogs)).rowcount
        e = db.execute(delete(LoginAttempt).where(LoginAttempt.timestamp < cutoff_login)).rowcount
        db.commit()

    log.info(
        "retention_ok",
        device_pings=a,
        webhook_events=b,
        collections=c,
        system_logs=d,
        login_attempts=e,
    )
