"""Job: ping every device with an IP and update the network status."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import Device, ExtensionLine
from middleware_monitor.core.time import as_local_str
from middleware_monitor.domain.config.repository import load_config
from middleware_monitor.domain.devices.repository import record_ping
from middleware_monitor.domain.extension_configurator import apply as ec_apply
from middleware_monitor.domain.extension_configurator import repository as ec_repo
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

    async def probe_one(
        device_id: int, ip: str, current_mac: str | None
    ) -> tuple[int, bool, int | None, str | None]:
        async with sem:
            latency = await ping.ping(ip, timeout_ms)
        online = latency is not None
        new_mac: str | None = None
        if online and current_mac is None:
            try:
                new_mac = await arp.lookup(ip)
            except Exception:
                new_mac = None
        return device_id, online, latency, new_mac

    tasks = [probe_one(d.id, d.ip, d.mac) for d in devices if d.ip]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    recovered_device_ids: list[int] = []
    with session_factory() as db:
        for r in results:
            if isinstance(r, Exception):
                continue
            device_id, online, latency, new_mac = r
            d = db.get(Device, device_id)
            if d is None:
                continue
            prev_status = d.network_status
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
                if prev_status == "offline":
                    recovered_device_ids.append(device_id)
            else:
                off += 1
        db.commit()

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info("monitor_ok", online=on, offline=off, total=len(devices), duration_ms=duration_ms)

    # ``data`` is a flat array — the shape the receiving application accepts.
    # The online/offline counts stay in the log line above, not in the payload.
    sender = WebhookSender(session_factory)
    await sender.dispatch("devices", snapshot)

    if recovered_device_ids and cfg.auto_reapply_on_recovery:
        await _trigger_recovery_reapplies(
            recovered_device_ids,
            debounce_minutes=cfg.auto_reapply_debounce_minutes,
        )


async def _trigger_recovery_reapplies(
    device_ids: list[int], *, debounce_minutes: int,
) -> None:
    """Para cada device que voltou (offline→online), dispara apply nas linhas
    vinculadas que não tiveram reapply 'recovery' recente.

    Regra crítica: o USCall (PBX) é a fonte da verdade do estado lógico do
    ramal. Se o device responde ICMP **e** está `logical_status='available'`
    no USCall, o telefone está provisionado corretamente — não tomamos ação,
    a config existente está funcionando. Só reaplicamos quando o device
    responde ICMP **mas** continua `unavailable` no PBX — sinal de que a
    config sumiu ou o ramal não está registrando.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=max(1, debounce_minutes))
    lines_to_apply: list[tuple[str, int, str]] = []  # (line_id, device_id, ramal)
    with session_factory() as db:
        for dev_id in device_ids:
            dev = db.get(Device, dev_id)
            if dev is None:
                continue
            if dev.logical_status == "available":
                log.info(
                    "recovery_skipped_uscall_available",
                    device_id=dev_id, device_name=dev.name,
                )
                continue
            lines = list(
                db.scalars(
                    select(ExtensionLine).where(ExtensionLine.device_id == dev_id)
                ).all()
            )
            for ln in lines:
                if not ln.ip:
                    continue
                last = ec_repo.last_reapply_event_for_line(db, ln.id)
                if (
                    last is not None
                    and last.reason == "recovery"
                    and last.started_at >= cutoff
                ):
                    log.info(
                        "recovery_skipped_debounce",
                        line_id=ln.id, device_id=dev_id,
                        last_at=last.started_at.isoformat(),
                    )
                    continue
                lines_to_apply.append((ln.id, dev_id, ln.numero_ramal))

    for line_id, dev_id, ramal in lines_to_apply:
        try:
            await ec_apply.run_apply_single_line(
                line_id, operador="auto-watcher", reason="recovery",
            )
            log.info(
                "recovery_reapply_dispatched",
                line_id=line_id, device_id=dev_id, ramal=ramal,
            )
        except Exception as exc:
            log.warning(
                "recovery_reapply_failed",
                line_id=line_id, device_id=dev_id, ramal=ramal,
                error=f"{type(exc).__name__}: {exc}",
            )
