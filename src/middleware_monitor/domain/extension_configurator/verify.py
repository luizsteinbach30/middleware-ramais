"""Verificação de registro SIP pós-aplicação (via USCall).

Depois de aplicar a config e o telefone reiniciar, confirma se o ramal voltou a
**registrar no PBX** consultando o USCall (`extenStatus`).

Multi-servidor (v2.7.0): a instalação pode ter N servidores USCall. Ramais não
se repetem entre eles (ADR-0003), então cada tentativa consulta TODOS os
servidores habilitados em paralelo e mescla os resultados — 1 request por
servidor por tentativa, N pequeno. Um servidor fora vira warning; a
verificação segue com os demais.

Custo de rede controlado:
  * **lote** (`verify_registration_batch`): UMA consulta da lista completa POR
    SERVIDOR por tentativa cobre todos os ramais do run — não dispara 1
    request por ramal.
  * **linha única** (`verify_registration_one`): consulta só aquele ramal
    (parâmetro ``ramal`` → payload mínimo) em cada servidor.

Registrado no PBX = ``status`` ≠ ``"indisponivel"`` (disponivel/ocupado/tocando/
discando indicam o aparelho registrado). Se nenhum USCall estiver configurado,
a verificação é pulada (``"skipped"`` / ``None``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.logging import get_logger
from middleware_monitor.domain.uscall import repository as uscall_repo
from middleware_monitor.integrations.uscall_client import UscallClient

log = get_logger("extension_configurator.verify")

DbFactory = Callable[[], AbstractContextManager[DBSession]]

# valores de `status` que significam "NÃO registrado" no PBX
_NOT_REGISTERED = {"", "indisponivel"}


def _is_registered(entry: dict[str, Any]) -> bool:
    return str(entry.get("status", "")).strip().lower() not in _NOT_REGISTERED


def _build_clients(db_factory: DbFactory) -> list[tuple[str, UscallClient]]:
    """Um cliente por servidor USCall habilitado (com token), ou lista vazia."""
    out: list[tuple[str, UscallClient]] = []
    with db_factory() as db:
        for srv in uscall_repo.list_servers(db, enabled_only=True):
            token = uscall_repo.load_server_token(srv)
            if not token:
                continue
            out.append(
                (
                    srv.nome,
                    UscallClient(
                        host=srv.host, token=token,
                        verify_ssl=srv.verify_ssl, timeout=10.0,
                    ),
                )
            )
    return out


async def _fetch_all_lists(
    clients: list[tuple[str, UscallClient]],
) -> list[dict[str, Any]]:
    """``fetch_extensions`` em todos os servidores em paralelo; falhas viram
    warning e são ignoradas (união dos que responderam)."""
    raw = await asyncio.gather(
        *(c.fetch_extensions() for _, c in clients), return_exceptions=True,
    )
    rows: list[dict[str, Any]] = []
    for (nome, _), r in zip(clients, raw, strict=True):
        if isinstance(r, BaseException):
            log.warning("verify_fetch_failed", server=nome, error=type(r).__name__)
            continue
        rows.extend(r)
    return rows


async def verify_registration_batch(
    db_factory: DbFactory,
    ramais: Iterable[str],
    *,
    attempts: int = 4,
    delay_s: float = 8.0,
) -> dict[str, str] | None:
    """Verifica vários ramais com uma consulta de lista POR SERVIDOR por
    tentativa.

    Retorna ``{ramal: "registered"|"unregistered"}`` ou ``None`` se nenhum
    USCall está configurado (verificação pulada).
    """
    targets = {str(r) for r in ramais if str(r).strip()}
    if not targets:
        return {}
    clients = _build_clients(db_factory)
    if not clients:
        return None

    result: dict[str, str] = dict.fromkeys(targets, "unregistered")
    pending = set(targets)
    for i in range(max(1, attempts)):
        if i > 0:
            await asyncio.sleep(delay_s)
        rows = await _fetch_all_lists(clients)
        if not rows:
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
    """Verifica UM ramal (payload mínimo em cada servidor).
    ``registered|unregistered|skipped``."""
    if not str(ramal).strip():
        return "skipped"
    clients = _build_clients(db_factory)
    if not clients:
        return "skipped"
    for i in range(max(1, attempts)):
        if i > 0:
            await asyncio.sleep(delay_s)
        raw = await asyncio.gather(
            *(c.fetch_extension(str(ramal)) for _, c in clients),
            return_exceptions=True,
        )
        for (nome, _), entry in zip(clients, raw, strict=True):
            if isinstance(entry, BaseException):
                log.warning(
                    "verify_one_fetch_failed",
                    ramal=ramal, server=nome, error=type(entry).__name__,
                )
                continue
            if entry is not None and _is_registered(entry):
                return "registered"
    return "unregistered"
