"""Repository for the ``devices`` and ``device_pings`` tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import Device, DevicePing


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def list_devices(
    db: DBSession,
    *,
    search: str | None = None,
    network: str | None = None,
    logical: str | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[Device], int]:
    stmt = select(Device)
    if search:
        like = f"%{search}%"
        stmt = stmt.where((Device.name.ilike(like)) | (Device.ip.ilike(like)))
    if network and network != "all":
        stmt = stmt.where(Device.network_status == network)
    if logical and logical != "all":
        stmt = stmt.where(Device.logical_status == logical)

    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        db.scalars(stmt.order_by(Device.name).offset((page - 1) * size).limit(size))
        .all()
    )
    return list(rows), total


def get_device(db: DBSession, device_id: int) -> Device | None:
    return db.get(Device, device_id)


def upsert_from_uscall(db: DBSession, payload: list[dict[str, Any]]) -> int:
    """Apply the upsert rules defined in REQUISITOS RF-12.

    Returns the number of rows touched (created or updated).
    """
    touched = 0
    now = _now()
    for ext in payload:
        name = str(ext.get("ramal") or "").strip()
        if not name:
            continue
        ip = (ext.get("ip") or "").strip() or None
        status_raw = (ext.get("status") or "").strip().lower()
        logical = "available" if status_raw == "disponivel" else "unavailable"

        existing = db.scalar(select(Device).where(Device.name == name))
        if existing is None:
            if logical == "available" and ip:
                db.add(
                    Device(
                        name=name,
                        ip=ip,
                        logical_status=logical,
                        network_status="unknown",
                        last_seen_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
                touched += 1
            continue

        if ip and ip != existing.ip:
            existing.ip = ip
        existing.logical_status = logical
        if logical == "available":
            existing.last_seen_at = now
        existing.updated_at = now
        touched += 1
    return touched


def record_ping(
    db: DBSession,
    device: Device,
    *,
    online: bool,
    latency_ms: int | None,
) -> None:
    now = _now()
    device.network_status = "online" if online else "offline"
    device.latency_ms = latency_ms if online else None
    device.last_ping_at = now
    device.updated_at = now
    db.add(DevicePing(device_id=device.id, timestamp=now, online=online, latency_ms=latency_ms))


def recent_pings(db: DBSession, device_id: int, *, limit: int = 50) -> list[DevicePing]:
    return list(
        db.scalars(
            select(DevicePing)
            .where(DevicePing.device_id == device_id)
            .order_by(DevicePing.timestamp.desc())
            .limit(limit)
        ).all()
    )


def history_aggregate(
    db: DBSession,
    device_id: int,
    *,
    window: str = "24h",
) -> tuple[str, list[tuple[datetime, float, float | None, float | None]]]:
    """Returns (granularity, points). Aggregations are computed in Python to
    keep SQLite-portable code; for very large windows consider pre-aggregated
    tables (TODO: device_pings_5m / _1h)."""
    now = _now()
    if window == "24h":
        since = now - timedelta(hours=24)
        granularity = "1m"
        bucket_seconds = 60
    elif window == "7d":
        since = now - timedelta(days=7)
        granularity = "5m"
        bucket_seconds = 5 * 60
    elif window == "30d":
        since = now - timedelta(days=30)
        granularity = "1h"
        bucket_seconds = 60 * 60
    else:
        since = now - timedelta(hours=24)
        granularity = "1m"
        bucket_seconds = 60

    rows = list(
        db.scalars(
            select(DevicePing)
            .where(DevicePing.device_id == device_id, DevicePing.timestamp >= since)
            .order_by(DevicePing.timestamp.asc())
        ).all()
    )

    buckets: dict[int, list[DevicePing]] = {}
    for r in rows:
        b = int(r.timestamp.timestamp() // bucket_seconds)
        buckets.setdefault(b, []).append(r)

    out: list[tuple[datetime, float, float | None, float | None]] = []
    for b in sorted(buckets):
        items = buckets[b]
        ts = datetime.fromtimestamp(b * bucket_seconds, tz=UTC).replace(tzinfo=None)
        on = sum(1 for x in items if x.online)
        ratio = on / len(items)
        latencies = [x.latency_ms for x in items if x.online and x.latency_ms is not None]
        avg = (sum(latencies) / len(latencies)) if latencies else None
        mx = float(max(latencies)) if latencies else None
        out.append((ts, ratio, avg, mx))
    return granularity, out


def status_counts(db: DBSession) -> dict[str, int]:
    n = int(db.scalar(select(func.count(Device.id))) or 0)
    on = int(db.scalar(select(func.count(Device.id)).where(Device.network_status == "online")) or 0)
    off = int(db.scalar(select(func.count(Device.id)).where(Device.network_status == "offline")) or 0)
    avail = int(
        db.scalar(select(func.count(Device.id)).where(Device.logical_status == "available")) or 0
    )
    unavail = int(
        db.scalar(select(func.count(Device.id)).where(Device.logical_status == "unavailable")) or 0
    )
    avg_lat = db.scalar(
        select(func.avg(Device.latency_ms)).where(Device.network_status == "online")
    )
    max_lat = db.scalar(
        select(func.max(Device.latency_ms)).where(Device.network_status == "online")
    )
    return {
        "total": n,
        "network_online": on,
        "network_offline": off,
        "logical_available": avail,
        "logical_unavailable": unavail,
        "avg_latency_ms": int(avg_lat) if avg_lat is not None else 0,
        "max_latency_ms": int(max_lat) if max_lat is not None else 0,
    }
