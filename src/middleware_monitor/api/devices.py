"""Devices endpoints (list, detail, history, force ping)."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import (
    get_current_user,
    get_session,
    require_admin,
    require_csrf,
)
from middleware_monitor.core.models import User
from middleware_monitor.domain.devices.repository import (
    get_device,
    history_aggregate,
    list_devices,
    record_ping,
    recent_pings,
    status_counts,
)
from middleware_monitor.domain.devices.schemas import (
    DeviceOut,
    DevicePingOut,
    DevicesPage,
    HistoryPoint,
    HistoryWindow,
)
from middleware_monitor.integrations.network import make_ping_probe
from middleware_monitor.jobs.monitor_devices import run_monitor_devices

router = APIRouter(prefix="/api/devices", tags=["devices"])

_FORCE_LAST_AT: dict[str, float] = {}
_FORCE_RATE_S = 60.0


@router.get("", response_model=DevicesPage)
def list_(
    search: str | None = None,
    network: str | None = None,
    logical: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> DevicesPage:
    rows, total = list_devices(
        db, search=search, network=network, logical=logical, page=page, size=size
    )
    return DevicesPage(
        items=[DeviceOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        page=page,
        size=size,
    )


class SummaryOut(BaseModel):
    total: int
    network_online: int
    network_offline: int
    logical_available: int
    logical_unavailable: int
    avg_latency_ms: int
    max_latency_ms: int


@router.get("/summary", response_model=SummaryOut)
def summary(
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> SummaryOut:
    return SummaryOut(**status_counts(db))


@router.get("/{device_id}", response_model=DeviceOut)
def detail(
    device_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> DeviceOut:
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    return DeviceOut.model_validate(d, from_attributes=True)


@router.get("/{device_id}/pings", response_model=list[DevicePingOut])
def pings(
    device_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[DevicePingOut]:
    rows = recent_pings(db, device_id, limit=limit)
    return [
        DevicePingOut(timestamp=p.timestamp, online=p.online, latency_ms=p.latency_ms)
        for p in rows
    ]


@router.get("/{device_id}/history", response_model=HistoryWindow)
def history(
    device_id: int,
    window: str = Query(default="24h"),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> HistoryWindow:
    granularity, points = history_aggregate(db, device_id, window=window)
    return HistoryWindow(
        granularity=granularity,
        points=[
            HistoryPoint(timestamp=t, online_ratio=ratio, latency_ms_avg=avg, latency_ms_max=mx)
            for t, ratio, avg, mx in points
        ],
    )


@router.post(
    "/{device_id}/refresh",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def refresh_one(device_id: int, db: DBSession = Depends(get_session)) -> dict[str, object]:
    d = get_device(db, device_id)
    if d is None:
        raise HTTPException(status_code=404, detail="not_found")
    if not d.ip:
        raise HTTPException(status_code=400, detail="device_without_ip")
    probe = make_ping_probe()
    started = time.perf_counter()
    latency = await probe.ping(d.ip, timeout_ms=1500)
    online = latency is not None
    record_ping(db, d, online=online, latency_ms=latency)
    db.commit()
    return {
        "online": online,
        "latency_ms": latency,
        "took_ms": int((time.perf_counter() - started) * 1000),
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds"),
    }


@router.post(
    "/force-monitor",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def force_monitor(user: User = Depends(require_admin)) -> dict[str, str]:
    last = _FORCE_LAST_AT.get(str(user.id), 0.0)
    if time.monotonic() - last < _FORCE_RATE_S:
        raise HTTPException(status_code=429, detail="rate_limited")
    _FORCE_LAST_AT[str(user.id)] = time.monotonic()
    asyncio.create_task(run_monitor_devices())
    return {"status": "scheduled"}
