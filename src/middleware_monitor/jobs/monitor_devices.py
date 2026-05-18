"""Job: ping every device with an IP and update the network status."""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import select

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import Device
from middleware_monitor.core.time import as_local_str
from middleware_monitor.domain.config.repository import load_config
from middleware_monitor.domain.devices.repository import record_ping
from middleware_monitor.domain.webhooks.sender import WebhookSender
from middleware_monitor.integrations.network import (
    make_arp_probe,
    make_ping_probe,
)

log = get_logger("monitor")


async def run_monitor_devices() -> None:
    started = time.perf_counter()
    with session_factory() as db:
        cfg = load_config(db)
        devices = list(
            db.scalars(select(Device).where(Device.ip.is_not(None))).all()
        )

    if not devices:
        log.info("monitor_skipped", reason="no_devices")
        return

    ping = make_ping_probe()
    arp = make_arp_probe()
    timeout_ms = cfg.ping_timeout_ms
    sem = asyncio.Semaphore(max(1, min(cfg.ping_concurrency, 200)))

    snapshot: list[dict[str, object]] = []
    on = 0
    off = 0

    async def probe_one(device_id: int, ip: str, current_mac: str | None) -> tuple[int, bool, int | None, str | None]:
        async with sem:
            latency = await ping.ping(ip, timeout_ms)
        online = latency is not None
        new_mac: str | None = None
        if online and current_mac is None:
            try:
                new_mac = await arp.lookup(ip)
            except Exception:  # noqa: BLE001
                new_mac = None
        return device_id, online, latency, new_mac

    tasks = [probe_one(d.id, d.ip, d.mac) for d in devices if d.ip]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    with session_factory() as db:
        for r in results:
            if isinstance(r, Exception):
                continue
            device_id, online, latency, new_mac = r
            d = db.get(Device, device_id)
            if d is None:
                continue
            record_ping(db, d, online=online, latency_ms=latency)
            if new_mac and not d.mac:
                d.mac = new_mac
            snapshot.append(
                {
                    "name": d.name,
                    "ip": d.ip,
                    "logical_status": d.logical_status,
                    "status": d.network_status,
                    "latency": d.latency_ms,
                    "last_ping": as_local_str(d.last_ping_at),
                    "mac": d.mac,
                }
            )
            if online:
                on += 1
            else:
                off += 1
        db.commit()

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info("monitor_ok", online=on, offline=off, total=len(devices), duration_ms=duration_ms)

    # ``data`` is a flat array — the shape the receiving application accepts.
    # The online/offline counts stay in the log line above, not in the payload.
    sender = WebhookSender(session_factory)
    await sender.dispatch("devices", snapshot)
