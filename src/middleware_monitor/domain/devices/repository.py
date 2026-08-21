"""Repository for the ``devices`` and ``device_pings`` tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import (
    Device,
    DevicePing,
    ExtensionLine,
    LineReapplyEvent,
)
from middleware_monitor.domain.mqtt.parser import logical_from_status, normalize_status


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _ip_to_int(ip: str | None) -> int | None:
    """IPv4 dotted-string → inteiro, ou None se não for um IPv4 válido."""
    if not ip:
        return None
    parts = ip.strip().split(".")
    if len(parts) != 4:
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 or n > 255 for n in nums):
        return None
    return (nums[0] << 24) | (nums[1] << 16) | (nums[2] << 8) | nums[3]


def _apply_range_filters(
    rows: list[Device],
    ip_from: str | None,
    ip_to: str | None,
    ramal_from: int | None,
    ramal_to: int | None,
) -> list[Device]:
    """Filtra por faixa de IP e/ou faixa de ramal, em Python.

    IP é comparado como inteiro (lexicográfico não serve para ranges); o ramal
    é ``Device.name`` quando for puramente numérico. Devices sem IP válido caem
    fora quando há filtro de IP; com nome não-numérico, fora quando há filtro de
    ramal.
    """
    lo_ip = _ip_to_int(ip_from) if ip_from else None
    hi_ip = _ip_to_int(ip_to) if ip_to else None
    if lo_ip is not None or hi_ip is not None:
        lo = lo_ip if lo_ip is not None else 0
        hi = hi_ip if hi_ip is not None else 0xFFFFFFFF
        kept = []
        for r in rows:
            iv = _ip_to_int(r.ip)
            if iv is not None and lo <= iv <= hi:
                kept.append(r)
        rows = kept
    if ramal_from is not None or ramal_to is not None:
        kept = []
        for r in rows:
            if not r.name.isdigit():
                continue
            n = int(r.name)
            if ramal_from is not None and n < ramal_from:
                continue
            if ramal_to is not None and n > ramal_to:
                continue
            kept.append(r)
        rows = kept
    return rows


def list_devices(
    db: DBSession,
    *,
    search: str | None = None,
    network: str | None = None,
    logical: str | None = None,
    environment: str | None = None,
    ip_from: str | None = None,
    ip_to: str | None = None,
    ramal_from: int | None = None,
    ramal_to: int | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[Device], int]:
    """Lista devices paginados aplicando os filtros disponíveis.

    ``environment``: id de um ambiente (devices com linha vinculada a ele) ou
    ``"none"``/``"unlinked"`` para devices sem nenhum vínculo. Os filtros de
    faixa (``ip_from``/``ip_to``/``ramal_from``/``ramal_to``) são aplicados em
    Python; quando ativos, a paginação também passa a ser feita em memória
    (custo aceitável na escala alvo — RNF-01, até ~1000 devices).
    """
    stmt = select(Device)
    if search:
        like = f"%{search}%"
        stmt = stmt.where((Device.name.ilike(like)) | (Device.ip.ilike(like)))
    if network and network != "all":
        stmt = stmt.where(Device.network_status == network)
    if logical and logical != "all":
        stmt = stmt.where(Device.logical_status == logical)
    if environment and environment != "all":
        if environment in ("none", "unlinked"):
            linked = select(ExtensionLine.device_id).where(
                ExtensionLine.device_id.is_not(None)
            )
            stmt = stmt.where(Device.id.not_in(linked))
        else:
            in_env = select(ExtensionLine.device_id).where(
                ExtensionLine.environment_id == environment,
                ExtensionLine.device_id.is_not(None),
            )
            stmt = stmt.where(Device.id.in_(in_env))

    range_active = any(
        v not in (None, "") for v in (ip_from, ip_to, ramal_from, ramal_to)
    )
    if not range_active:
        total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        rows = db.scalars(
            stmt.order_by(Device.name).offset((page - 1) * size).limit(size)
        ).all()
        return list(rows), total

    all_rows = list(db.scalars(stmt.order_by(Device.name)).all())
    filtered = _apply_range_filters(all_rows, ip_from, ip_to, ramal_from, ramal_to)
    total = len(filtered)
    start = (page - 1) * size
    return filtered[start : start + size], total


def delete_devices(db: DBSession, ids: list[int]) -> int:
    """Apaga devices em massa. Desvincula as ExtensionLines (device_id→NULL),
    zera o device_id de reapply events e remove os pings. Preserva linhas e
    ambientes. Retorna a quantidade de devices removidos."""
    if not ids:
        return 0
    now = _now()
    db.execute(
        update(ExtensionLine)
        .where(ExtensionLine.device_id.in_(ids))
        .values(device_id=None, updated_at=now)
    )
    db.execute(
        update(LineReapplyEvent)
        .where(LineReapplyEvent.device_id.in_(ids))
        .values(device_id=None)
    )
    db.execute(delete(DevicePing).where(DevicePing.device_id.in_(ids)))
    deleted = int(
        db.scalar(select(func.count()).select_from(Device).where(Device.id.in_(ids)))
        or 0
    )
    db.execute(delete(Device).where(Device.id.in_(ids)))
    return deleted


def get_device(db: DBSession, device_id: int) -> Device | None:
    return db.get(Device, device_id)


def upsert_from_uscall(
    db: DBSession,
    payload: list[dict[str, Any]],
    *,
    server_id_by_ramal: dict[str, int] | None = None,
) -> int:
    """Apply the upsert rules defined in REQUISITOS RF-12.

    Returns the number of rows touched (created or updated). Após o upsert:

    * ``link_extension_lines_to_device`` vincula ExtensionLines órfãs com IP
      igual ao do device (idempotente).
    * Devices cujo IP mudou propagam o novo IP para as linhas vinculadas
      (mantém a planilha em sincronia automaticamente — o USCall é fonte da
      verdade do endpoint atual do telefone).
    * ``server_id_by_ramal`` (multi-USCall): grava a origem da coleta em
      ``device.uscall_server_id`` — observabilidade e insumo do verify.
    """
    touched = 0
    now = _now()
    by_server = server_id_by_ramal or {}
    touched_devices: list[Device] = []
    ip_changed_devices: list[Device] = []
    for ext in payload:
        name = str(ext.get("ramal") or "").strip()
        if not name:
            continue
        ip = (ext.get("ip") or "").strip() or None
        status_raw = (ext.get("status") or "").strip()
        # "Tocando"/"Ocupado"/"Discando" significam ramal **registrado** e em uso.
        # Marcá-los `unavailable` faria `jobs/monitor_devices.py` entender "config
        # perdida" e reaplicar a configuração no telefone durante a ligação — ver
        # `domain/mqtt/parser.logical_from_status`, que é a mesma regra usada pelo
        # caminho MQTT para as duas fontes não contarem histórias diferentes.
        logical = logical_from_status(normalize_status(status_raw)) or "unavailable"
        server_id = by_server.get(name)

        existing = db.scalar(select(Device).where(Device.name == name))
        if existing is None:
            if logical == "available" and ip:
                new_dev = Device(
                    name=name,
                    ip=ip,
                    logical_status=logical,
                    status_source="uscall",
                    network_status="unknown",
                    uscall_server_id=server_id,
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
        existing.status_source = "uscall"
        if server_id is not None and existing.uscall_server_id != server_id:
            existing.uscall_server_id = server_id
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
