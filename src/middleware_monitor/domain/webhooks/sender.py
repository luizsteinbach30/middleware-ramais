"""Webhook sender with retry/backoff and full audit trail in ``webhook_events``.

Public surface:
* ``WebhookSender(db_factory).dispatch(event_type, payload, ...)`` — main path.
* ``WebhookSender.replay(event_id)`` — re-send an existing event.
* ``WebhookSender.dispatch_test(event_type, sample)`` — explicit ``test=true``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import WebhookEvent
from middleware_monitor.core.time import now_local_str
from middleware_monitor.domain.config.repository import (
    load_config,
    load_secret,
    update_webhook_status,
)
from middleware_monitor.version import __version__

log = get_logger("webhook")

RETRY_DELAYS_S = (0, 5, 30)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _wrap_payload(event_type: str, data: Any, *, client_code: str, is_test: bool) -> dict[str, Any]:
    """Build the webhook envelope. ``data`` is forwarded as-is — for
    ``devices``/``extensions`` it is a flat array, which is the shape the
    receiving application accepts. ``timestamp`` is local wall-clock time
    (``'YYYY-MM-DD HH:MM:SS'``, no ``T``/``Z``)."""
    return {
        "event_type": event_type,
        "client_code": client_code,
        "timestamp": now_local_str(),
        "data": data,
        "test": is_test,
    }


class WebhookSender:
    def __init__(self, db_factory: Callable[[], DBSession]) -> None:
        self._db_factory = db_factory

    async def dispatch(
        self,
        event_type: str,
        data: Any,
        *,
        is_test: bool = False,
        is_replay: bool = False,
        replay_of: int | None = None,
    ) -> WebhookEvent | None:
        with self._db_factory() as db:
            cfg = load_config(db)
            wh = cfg.webhooks.get(event_type)
            if wh is None or not wh.enabled or not wh.url:
                log.info("webhook_skipped", event_type=event_type, reason="disabled_or_missing")
                return None
            url = wh.url
            token = load_secret(db, f"webhooks.{event_type}.token") if wh.token == "set" else None
            timeout = cfg.webhook_timeout_seconds
            wrapped = _wrap_payload(event_type, data, client_code=cfg.client_code, is_test=is_test)

        last: WebhookEvent | None = None
        total = len(RETRY_DELAYS_S)

        for attempt, delay in enumerate(RETRY_DELAYS_S, start=1):
            if delay:
                await asyncio.sleep(delay)
            event = await self._post(
                url=url,
                token=token,
                wrapped=wrapped,
                event_type=event_type,
                attempt=attempt,
                total=total,
                timeout=timeout,
                is_test=is_test,
                is_replay=is_replay,
                replay_of=replay_of,
            )
            last = event
            if event.success:
                self._record_status(event_type, ok=True, ts=event.timestamp)
                return event

        if last is not None:
            self._record_status(event_type, ok=False, ts=last.timestamp)
        return last

    async def dispatch_test(self, event_type: str) -> WebhookEvent | None:
        # ``data`` is always an array, mirroring the real devices/extensions
        # payloads, so the receiver can use the same parser for test events.
        sample = [
            {
                "name": "3660",
                "ip": "10.20.30.40",
                "logical_status": "disponivel",
                "status": "online",
                "latency": 12,
                "last_ping": now_local_str(),
                "mac": "00:11:22:33:44:55",
            }
        ]
        return await self.dispatch(event_type, sample, is_test=True)

    async def replay(self, event_id: int) -> WebhookEvent | None:
        with self._db_factory() as db:
            event = db.get(WebhookEvent, event_id)
            if event is None:
                return None
            try:
                payload = json.loads(event.payload)
                data = payload.get("data")
                event_type = event.event_type
            except Exception:
                return None
        return await self.dispatch(event_type, data, is_test=False, is_replay=True, replay_of=event_id)

    # ------------------------------------------------------------------ internal

    async def _post(
        self,
        *,
        url: str,
        token: str | None,
        wrapped: dict[str, Any],
        event_type: str,
        attempt: int,
        total: int,
        timeout: float,
        is_test: bool,
        is_replay: bool,
        replay_of: int | None,
    ) -> WebhookEvent:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"MiddlewareMonitor/{__version__}",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        started = time.perf_counter()
        http_status: int | None = None
        body_text: str | None = None
        success = False
        error: str | None = None

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=wrapped, headers=headers)
            http_status = resp.status_code
            body_text = resp.text[:8000] if resp.text else None
            success = 200 <= resp.status_code < 300
        except httpx.HTTPError as exc:
            error = type(exc).__name__
        duration_ms = int((time.perf_counter() - started) * 1000)

        log.info(
            "webhook_attempt",
            event_type=event_type,
            attempt=attempt,
            total=total,
            success=success,
            http_status=http_status,
            duration_ms=duration_ms,
            error=error,
        )

        return self._persist(
            event_type=event_type,
            url=url,
            wrapped=wrapped,
            http_status=http_status,
            body_text=body_text,
            success=success,
            error=error,
            duration_ms=duration_ms,
            attempt=attempt,
            total=total,
            is_test=is_test,
            is_replay=is_replay,
            replay_of=replay_of,
        )

    def _persist(
        self,
        *,
        event_type: str,
        url: str,
        wrapped: dict[str, Any],
        http_status: int | None,
        body_text: str | None,
        success: bool,
        error: str | None,
        duration_ms: int,
        attempt: int,
        total: int,
        is_test: bool,
        is_replay: bool,
        replay_of: int | None,
    ) -> WebhookEvent:
        with self._db_factory() as db:
            event = WebhookEvent(
                timestamp=_now(),
                event_type=event_type,
                url=url,
                http_status=http_status,
                duration_ms=duration_ms,
                success=success,
                error=error,
                payload=json.dumps(wrapped, ensure_ascii=False),
                response_body=body_text,
                attempt=attempt,
                total_attempts=total,
                is_test=is_test,
                is_replay=is_replay,
                replay_of=replay_of,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            return event

    def _record_status(self, event_type: str, *, ok: bool, ts: datetime) -> None:
        suffix = ts.isoformat(timespec="seconds")
        with self._db_factory() as db:
            update_webhook_status(db, event_type, f"{'OK' if ok else 'FAIL'} em {suffix}")
