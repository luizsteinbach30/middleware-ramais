"""Job: collect USCall extension status snapshot."""

from __future__ import annotations

import time

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.domain.collections.repository import save_snapshot
from middleware_monitor.domain.config.repository import load_config, load_secret
from middleware_monitor.domain.devices.repository import upsert_from_uscall
from middleware_monitor.domain.webhooks.sender import WebhookSender
from middleware_monitor.integrations.uscall_client import (
    UscallAuthError,
    UscallClient,
    UscallError,
)

log = get_logger("collector")


async def run_collect_extensions() -> None:
    started = time.perf_counter()
    with session_factory() as db:
        cfg = load_config(db)
        if not cfg.uscall_host or cfg.uscall_token is None:
            log.info("collect_skipped", reason="not_configured")
            return
        token = load_secret(db, "uscall_token")
    if not token:
        log.warning("collect_skipped", reason="missing_token")
        return

    client = UscallClient(
        host=cfg.uscall_host,
        token=token,
        verify_ssl=cfg.uscall_verify_ssl,
        timeout=10.0,
    )
    try:
        payload = await client.fetch_extensions()
    except UscallAuthError:
        log.warning("collect_failed", reason="auth")
        return
    except UscallError as exc:
        log.warning("collect_failed", reason=type(exc).__name__, message=str(exc))
        return

    with session_factory() as db:
        snap = save_snapshot(db, type_="extensions", payload=payload)
        touched = upsert_from_uscall(db, payload)
        db.commit()

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "collect_ok",
        count=len(payload),
        snapshot_id=snap.id,
        upserted=touched,
        duration_ms=duration_ms,
    )

    sender = WebhookSender(session_factory)
    await sender.dispatch("extensions", payload)
