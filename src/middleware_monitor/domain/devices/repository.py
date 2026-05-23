"""Repository for the ``devices`` and ``device_pings`` tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import Device, DevicePing, ExtensionLine


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

    Returns the number of rows touched (created or updated). Após o upsert:

    * ``link_extension_lines_to_device`` vincula ExtensionLines órfãs com IP
      igual ao do device (idempotente).
    * Devices cujo IP mudou propagam o novo IP para as linhas vinculadas
      (mantém a planilha em sincronia automaticamente — o USCall é fonte da
      verdade do endpoint atual do telefone).
    """
    touched = 0
    now = _now()
    touched_devices: list[Device] = []
    ip_changed_devices: list[Device] = []
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
                new_dev = Device(
                    name=name,
                    ip=ip,
                    logical_status=logical,
                    network_status="unknown",
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(new_dev)
                touched_devices.append(new_dev)
                touched += 1
            continue

        if ip and ip != existing.ip:
            existing.ip = ip
            ip_changed_devices.append(existing)
        existing.logical_status = logical
        if logical == "available":
            existing.last_seen_at = now
        existing.updated_at = now
        touched_devices.append(existing)
        touched += 1

    if touched_devices:
        db.flush()  # garante .id para os devices novos
        for dev in ip_changed_devices:
            sync_linked_lines_ip(db, dev)
        for dev in touched_devices:
            link_extension_lines_to_device(db, dev)
    return touched


def sync_linked_lines_ip(db: DBSession, device: Device) -> int:
    """Para cada ExtensionLine vinculada a este device, atualiza ``line.ip``
    para ``device.ip``.

    Use quando o IP do device muda (DHCP refresh, troca de rede). Sem isso
    a planilha do ambiente continuaria com o IP antigo e a próxima aplicação
    enviaria a config para o lugar errado.

    Idempotente: linhas já com o IP atual ficam intactas. Retorna a contagem
    de linhas efetivamente alteradas.
    """
    if not device.ip:
        return 0
    rows = list(
        db.scalars(
            select(ExtensionLine).where(
                ExtensionLine.device_id == device.id,
                ExtensionLine.ip != device.ip,
            )
        ).all()
    )
    now = _now()
    for ln in rows:
        ln.ip = device.ip
        ln.updated_at = now
    if rows:
        db.flush()
    return len(rows)


def link_extension_lines_to_device(db: DBSession, device: Device) -> int:
    """Vincula ExtensionLines órfãs (device_id IS NULL) com IP igual ao do device.

    Retorna a quantidade vinculada nesta chamada. Idempotente.
    """
    if not device.ip:
        return 0
    rows = list(
        db.scalars(
            select(ExtensionLine).where(
                ExtensionLine.device_id.is_(None),
                ExtensionLine.ip == device.ip,
            )
        ).all()
    )
    now = _now()
    for ln in rows:
        ln.device_id = device.id
        ln.updated_at = now
    if rows:
        db.flush()
    return len(rows)


def record_ping(
    db: DBSession,
    device: Device,
    *,
    online: bool,
    latency_ms: int | None,
) -> None:
    now = _now()
    device.network_status_prev = device.network_status
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
