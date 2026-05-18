"""Webhook events endpoints (list, detail, test, replay, send-now)."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import (
    get_current_user,
    get_session,
    require_admin,
    require_csrf,
)
from middleware_monitor.core.db import session_factory
from middleware_monitor.core.models import User, WebhookEvent
from middleware_monitor.core.tasks import spawn
from middleware_monitor.core.time import iso_utc
from middleware_monitor.domain.collections.repository import latest as latest_snapshot
from middleware_monitor.domain.webhooks.sender import WebhookSender

router = APIRouter(prefix="/api", tags=["webhooks"])

_SEND_LAST_AT: dict[str, float] = {}
_SEND_RATE_S = 15.0


class WebhookEventOut(BaseModel):
    id: int
    timestamp: str
    event_type: str
    url: str
    http_status: int | None
    duration_ms: int
    success: bool
    error: str | None
    attempt: int
    total_attempts: int
    is_test: bool
    is_replay: bool


class WebhookEventsPage(BaseModel):
    items: list[WebhookEventOut]
    total: int
    page: int
    size: int


@router.get("/webhook-events", response_model=WebhookEventsPage)
def list_(
    type_: str | None = Query(default=None, alias="type"),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> WebhookEventsPage:
    stmt = select(WebhookEvent)
    if type_ and type_ != "all":
        stmt = stmt.where(WebhookEvent.event_type == type_)
    if status == "success":
        stmt = stmt.where(WebhookEvent.success.is_(True))
    elif status == "failed":
        stmt = stmt.where(WebhookEvent.success.is_(False))
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(WebhookEvent.timestamp.desc())
            .offset((page - 1) * size)
            .limit(size)
        ).all()
    )
    return WebhookEventsPage(
        items=[
            WebhookEventOut(
                id=r.id,
                timestamp=iso_utc(r.timestamp) or "",
                event_type=r.event_type,
                url=r.url,
                http_status=r.http_status,
                duration_ms=r.duration_ms,
                success=r.success,
                error=r.error,
                attempt=r.attempt,
                total_attempts=r.total_attempts,
                is_test=r.is_test,
                is_replay=r.is_replay,
            )
            for r in rows
        ],
        total=total,
        page=page,
        size=size,
    )


@router.get("/webhook-events/{event_id}")
def detail(
    event_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, object]:
    e = db.get(WebhookEvent, event_id)
    if e is None:
        raise HTTPException(status_code=404, detail="not_found")
    try:
        payload = json.loads(e.payload)
    except json.JSONDecodeError:
        payload = e.payload
    return {
        "id": e.id,
        "timestamp": iso_utc(e.timestamp),
        "event_type": e.event_type,
        "url": e.url,
        "http_status": e.http_status,
        "duration_ms": e.duration_ms,
        "success": e.success,
        "error": e.error,
        "attempt": e.attempt,
        "total_attempts": e.total_attempts,
        "is_test": e.is_test,
        "is_replay": e.is_replay,
        "payload": payload,
        "response_body": e.response_body,
    }


@router.post(
    "/webhooks/test/{event_type}",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def send_test(event_type: str) -> dict[str, object]:
    if event_type not in {"extensions", "devices", "results"}:
        raise HTTPException(status_code=400, detail="invalid_event_type")
    sender = WebhookSender(session_factory)
    event = await sender.dispatch_test(event_type)
    if event is None:
        return {"ok": False, "reason": "disabled_or_missing"}
    return {"ok": event.success, "event_id": event.id, "http_status": event.http_status}


@router.post(
    "/webhooks/send/{event_type}",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def send_now(
    event_type: str,
    user: User = Depends(require_admin),
    db: DBSession = Depends(get_session),
) -> dict[str, object]:
    """Dispatch a webhook out-of-band with the latest *real* payload (not a
    test sample). For ``extensions`` and ``results`` reads the most recent
    snapshot; for ``devices`` triggers a fresh ``monitor_devices`` run,
    which itself dispatches the webhook when it finishes."""
    if event_type not in {"extensions", "devices", "results"}:
        raise HTTPException(status_code=400, detail="invalid_event_type")

    last = _SEND_LAST_AT.get(f"{user.id}:{event_type}", 0.0)
    if time.monotonic() - last < _SEND_RATE_S:
        raise HTTPException(status_code=429, detail="rate_limited")
    _SEND_LAST_AT[f"{user.id}:{event_type}"] = time.monotonic()

    if event_type == "devices":
        # The devices payload is computed at monitor time, not stored as a
        # snapshot. Just kick the regular job — it pings every device and
        # ends with the same WebhookSender.dispatch("devices", ...).
        from middleware_monitor.jobs.monitor_devices import run_monitor_devices

        spawn(run_monitor_devices())
        return {"ok": True, "mode": "monitor_triggered"}

    snap = latest_snapshot(db, type_=event_type)
    if snap is None:
        return {"ok": False, "reason": "no_snapshot_yet"}
    try:
        payload = json.loads(snap.payload)
    except json.JSONDecodeError:
        payload = snap.payload

    sender = WebhookSender(session_factory)
    event = await sender.dispatch(event_type, payload)
    if event is None:
        return {"ok": False, "reason": "disabled_or_missing"}
    return {
        "ok": event.success,
        "event_id": event.id,
        "http_status": event.http_status,
        "snapshot_id": snap.id,
    }


@router.post(
    "/webhook-events/{event_id}/replay",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def replay(event_id: int) -> dict[str, object]:
    sender = WebhookSender(session_factory)
    event = await sender.replay(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="not_found_or_invalid")
    return {"ok": event.success, "new_event_id": event.id, "http_status": event.http_status}
