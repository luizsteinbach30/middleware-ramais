"""Job: collect USCall extension status snapshot (multi-servidor).

Coleta de TODOS os servidores USCall habilitados em paralelo; um servidor fora
não derruba a coleta dos demais (falha parcial vira warning por servidor). Os
payloads são mesclados por união — ramais não se repetem entre servidores
(premissa do cliente; ver ADR-0003) — e cada item ganha o campo ADITIVO
``uscall_server`` com o nome do servidor de origem.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.domain.collections.repository import save_snapshot
from middleware_monitor.domain.devices.repository import upsert_from_uscall
from middleware_monitor.domain.uscall import repository as uscall_repo
from middleware_monitor.domain.webhooks.sender import WebhookSender
from middleware_monitor.integrations.uscall_client import UscallClient

log = get_logger("collector")


def merge_payloads(
    results: list[tuple[int, str, list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """União dos payloads de N servidores → (payload_mesclado, {ramal: server_id}).

    Se um ramal aparecer em dois servidores (não deveria — premissa do
    cliente), loga warning e o primeiro vence. O campo ``uscall_server`` é
    aditivo: o receptor do webhook ignora chaves extras, então o contrato
    flat existente é preservado.
    """
    merged: list[dict[str, Any]] = []
    server_id_by_ramal: dict[str, int] = {}
    seen: dict[str, str] = {}  # ramal -> nome do servidor que o forneceu
    for server_id, server_nome, rows in results:
        for row in rows:
            ramal = str(row.get("ramal") or "").strip()
            if ramal and ramal in seen:
                log.warning(
                    "collect_duplicate_ramal",
                    ramal=ramal, kept=seen[ramal], dropped=server_nome,
                )
                continue
            item = dict(row)
            item["uscall_server"] = server_nome
            merged.append(item)
            if ramal:
                seen[ramal] = server_nome
                server_id_by_ramal[ramal] = server_id
    return merged, server_id_by_ramal


async def _fetch_server(
    server_id: int, nome: str, host: str, token: str, verify_ssl: bool,
) -> tuple[int, str, list[dict[str, Any]]]:
    client = UscallClient(host=host, token=token, verify_ssl=verify_ssl, timeout=10.0)
    rows = await client.fetch_extensions()
    return server_id, nome, rows


async def run_collect_extensions() -> None:
    started = time.perf_counter()
    with session_factory() as db:
        servers = uscall_repo.list_servers(db, enabled_only=True)
        creds: list[tuple[int, str, str, str, bool]] = []
        for s in servers:
            token = uscall_repo.load_server_token(s)
            if not token:
                log.warning("collect_server_skipped", server=s.nome, reason="missing_token")
                continue
            creds.append((s.id, s.nome, s.host, token, s.verify_ssl))
    if not creds:
        log.info("collect_skipped", reason="not_configured")
        return

    raw = await asyncio.gather(
        *(_fetch_server(*c) for c in creds), return_exceptions=True,
    )
    results: list[tuple[int, str, list[dict[str, Any]]]] = []
    per_server: dict[str, int] = {}
    for cred, r in zip(creds, raw, strict=True):
        if isinstance(r, BaseException):
            # Falha parcial: loga e segue — os demais servidores continuam.
            log.warning(
                "collect_server_failed",
                server=cred[1], reason=type(r).__name__, message=str(r),
            )
            continue
        results.append(r)
        per_server[r[1]] = len(r[2])
    if not results:
        # TODOS fora: sem snapshot e sem webhook (comportamento da falha total).
        log.warning("collect_failed", reason="all_servers_failed")
        return

    payload, server_id_by_ramal = merge_payloads(results)

    with session_factory() as db:
        snap = save_snapshot(db, type_="extensions", payload=payload)
        touched = upsert_from_uscall(db, payload, server_id_by_ramal=server_id_by_ramal)
        db.commit()

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "collect_ok",
        count=len(payload),
        servers=per_server,
        snapshot_id=snap.id,
        upserted=touched,
        duration_ms=duration_ms,
    )

    sender = WebhookSender(session_factory)
    await sender.dispatch("extensions", payload)
