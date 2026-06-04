"""Verificação de registro SIP pós-aplicação (via USCall).

Depois de aplicar a config e o telefone reiniciar, confirma se o ramal voltou a
**registrar no PBX** consultando o USCall (`extenStatus`).

Custo de rede controlado:
  * **lote** (`verify_registration_batch`): UMA consulta da lista completa por
    tentativa cobre todos os ramais do run — não dispara 1 request por ramal.
  * **linha única** (`verify_registration_one`): consulta só aquele ramal
    (parâmetro ``ramal`` → payload mínimo).

Registrado no PBX = ``status`` ≠ ``"indisponivel"`` (disponivel/ocupado/tocando/
discando indicam o aparelho registrado). Se o USCall não estiver configurado,
a verificação é pulada (``"skipped"`` / ``None``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.logging import get_logger
from middleware_monitor.domain.config.repository import load_config, load_secret
from middleware_monitor.integrations.uscall_client import UscallClient, UscallError

log = get_logger("extension_configurator.verify")

DbFactory = Callable[[], AbstractContextManager[DBSession]]

# valores de `status` que significam "NÃO registrado" no PBX
_NOT_REGISTERED = {"", "indisponivel"}


def _is_registered(entry: dict[str, Any]) -> bool:
    return str(entry.get("status", "")).strip().lower() not in _NOT_REGISTERED


def _build_client(db_factory: DbFactory) -> UscallClient | None:
    """Monta o cliente USCall a partir da config salva, ou ``None`` se ausente."""
    with db_factory() as db:
        cfg = load_config(db)
        host = cfg.uscall_host
        verify_ssl = cfg.uscall_verify_ssl
        if not host:
            return None
        token = load_secret(db, "uscall_token")
    if not token:
        return None
    return UscallClient(host=host, token=token, verify_ssl=verify_ssl, timeout=10.0)


async def verify_registration_batch(
    db_factory: DbFactory,
    ramais: Iterable[str],
    *,
    attempts: int = 4,
    delay_s: float = 8.0,
) -> dict[str, str] | None:
    """Verifica vários ramais com **uma** consulta de lista completa por tentativa.

    Retorna ``{ramal: "registered"|"unregistered"}`` ou ``None`` se o USCall não
    está configurado (verificação pulada).
    """
    targets = {str(r) for r in ramais if str(r).strip()}
    if not targets:
        return {}
    client = _build_client(db_factory)
    if client is None:
        return None

    result: dict[str, str] = dict.fromkeys(targets, "unregistered")
    pending = set(targets)
    for i in range(max(1, attempts)):
        if i > 0:
            await asyncio.sleep(delay_s)
        try:
            rows = await client.fetch_extensions()  # 1 request cobre todos
        except UscallError as exc:
            log.warning("verify_batch_fetch_failed", error=type(exc).__name__)
            continue
        by_ramal = {str(row.get("ramal", "")): row for row in rows}
        for r in list(pending):
            entry = by_ramal.get(r)
            if entry is not None and _is_registered(entry):
                result[r] = "registered"
                pending.discard(r)
        if not pending:
            break
    return result


async def verify_registration_one(
    db_factory: DbFactory,
    ramal: str,
    *,
    attempts: int = 4,
    delay_s: float = 8.0,
) -> str:
    """Verifica UM ramal (payload mínimo). ``registered|unregistered|skipped``."""
    if not str(ramal).strip():
        return "skipped"
    client = _build_client(db_factory)
    if client is None:
        return "skipped"
    for i in range(max(1, attempts)):
        if i > 0:
            await asyncio.sleep(delay_s)
        try:
            entry = await client.fetch_extension(str(ramal))
        except UscallError as exc:
            log.warning("verify_one_fetch_failed", ramal=ramal, error=type(exc).__name__)
            continue
        if entry is not None and _is_registered(entry):
            return "registered"
    return "unregistered"
