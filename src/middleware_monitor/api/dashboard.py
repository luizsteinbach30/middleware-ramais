"""Dashboard summary + 24h timeseries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import get_current_user, get_session
from middleware_monitor.core.models import (
    Collection,
    Device,
    DevicePing,
    User,
    WebhookEvent,
)
from middleware_monitor.domain.devices.repository import status_counts

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


class DashboardSummary(BaseModel):
    total: int
    network_online: int
    network_offline: int
    logical_available: int
    logical_unavailable: int
    avg_latency_ms: int
    max_latency_ms: int
    last_collection_at: str | None
    webhooks_24h: int
    webhooks_24h_ok: int
    webhooks_24h_fail: int


class TimePoint(BaseModel):
    timestamp: str
    online: int
    offline: int


@router.get("/summary", response_model=DashboardSummary)
def summary(
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> DashboardSummary:
    counts = status_counts(db)
    last = db.scalar(select(Collection).order_by(Collection.collected_at.desc()).limit(1))

    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
    total_w = int(
        db.scalar(select(func.count(WebhookEvent.id)).where(WebhookEvent.timestamp >= since)) or 0
    )
    ok_w = int(
        db.scalar(
            select(func.count(WebhookEvent.id)).where(
                WebhookEvent.timestamp >= since, WebhookEvent.success.is_(True)
            )
        )
        or 0
    )
    return DashboardSummary(
        **counts,
        last_collection_at=last.collected_at.isoformat(timespec="seconds") if last else None,
        webhooks_24h=total_w,
        webhooks_24h_ok=ok_w,
        webhooks_24h_fail=total_w - ok_w,
    )


@router.get("/timeseries", response_model=list[TimePoint])
def timeseries(
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
    window_hours: int = Query(default=24, ge=1, le=168),
) -> list[TimePoint]:
    bucket = 15 * 60
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=window_hours)
    rows = list(
        db.scalars(
            select(DevicePing).where(DevicePing.timestamp >= since)
            .order_by(DevicePing.timestamp.asc())
        ).all()
    )
    total_devices = int(db.scalar(select(func.count(Device.id))) or 0)
    buckets: dict[int, list[bool]] = {}
    for r in rows:
        b = int(r.timestamp.timestamp() // bucket)
        buckets.setdefault(b, []).append(r.online)
    out: list[TimePoint] = []
    for b in sorted(buckets):
        items = buckets[b]
        on = sum(1 for x in items if x)
        ts = datetime.fromtimestamp(b * bucket, tz=UTC).replace(tzinfo=None).isoformat(timespec="seconds")
        out.append(TimePoint(timestamp=ts, online=on, offline=max(0, total_devices - on)))
    return out
