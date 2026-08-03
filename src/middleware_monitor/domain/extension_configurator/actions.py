"""Ações remotas nos telefones (device actions) — v2.7.0.

"Normalizar telefone" (volume máx + DND off) e demais ações remotas. Resolve o
adapter pelo modelo do ambiente, tenta a chain de credenciais (atual → nova) e
grava auditoria em ``device_action_events``.

Homologação e mecanismo por fabricante: docs/design/DEVICE_ACTIONS_HOMOLOGACAO.md.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import DeviceActionEvent
from middleware_monitor.core.tasks import spawn
from middleware_monitor.integrations.extension_configurator.vendors.base import (
    ActionResult,
    VendorActionUnsupported,
    VendorAuthError,
    VendorCredentials,
)

from . import action_state
from . import repository as repo
from .action_state import ActionRowState, ActionRunState
from .apply import build_creds_chain
from .service import adapter_for

log = get_logger("extension_configurator.actions")

# concorrência do "normalizar em massa" — telefones em paralelo, mas com teto
# para não saturar a rede nem o servidor.
_BULK_CONCURRENCY = 5


def capabilities_for(modelo_telefone: str) -> list[str]:
    """Ações que o vendor do modelo homologou (para a UI mostrar só o suportado)."""
    return sorted(adapter_for(modelo_telefone).capabilities())


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _record(
    *,
    environment_id: str | None,
    line_id: str | None,
    device_id: int | None,
    ip: str,
    vendor: str,
    action: str,
    params: dict[str, Any] | None,
    status: str,
    erro: str | None,
    operador: str | None,
) -> None:
    with session_factory() as db:
        db.add(
            DeviceActionEvent(
                timestamp=_now(),
                environment_id=environment_id,
                line_id=line_id,
                device_id=device_id,
                ip=ip,
                vendor=vendor,
                action=action,
                params_json=json.dumps(params, ensure_ascii=False) if params else None,
                status=status,
                erro=erro,
                operador=operador,
            )
        )
        db.commit()


async def _execute_with_fallback(
    adapter: Any, ip: str, chain: list[VendorCredentials], action: str, params: dict[str, Any],
) -> ActionResult:
    """Executa a ação tentando cada credencial da chain até uma funcionar."""
    last_auth_error: Exception | None = None
    for idx, creds in enumerate(chain):
        try:
            result: ActionResult = await adapter.execute_action(ip, creds, action, params)
            if idx > 0:
                log.info("action_auth_fallback_ok", ip=ip, cred_index=idx)
            return result
        except VendorAuthError as exc:
            last_auth_error = exc
            continue
    raise last_auth_error or VendorAuthError("device: nenhuma credencial aceita")


async def run_action_on_line(
    line_id: str, action: str, *, params: dict[str, Any] | None = None,
    operador: str | None = None,
) -> ActionResult:
    """Executa uma ação numa linha (telefone). Grava auditoria sempre."""
    params = params or {}
    with session_factory() as db:
        line = repo.get_line(db, line_id)
        if line is None:
            raise ValueError("linha nao encontrada")
        env = line.environment
        modelo = env.modelo_telefone
        cfg = repo.merged_config_padrao(env)
        env_id: str = env.id
        device_id: int | None = line.device_id
        ip: str = line.ip
    adapter = adapter_for(modelo)
    if action not in adapter.capabilities():
        raise VendorActionUnsupported(f"{adapter.vendor_id}: ação {action!r} não homologada")
    chain = build_creds_chain(cfg)
    try:
        result = await _execute_with_fallback(adapter, ip, chain, action, params)
    except Exception as exc:
        _record(
            environment_id=env_id, line_id=line_id, device_id=device_id, ip=ip,
            vendor=adapter.vendor_id, action=action, params=params,
            status="erro", erro=f"{type(exc).__name__}: {exc}", operador=operador,
        )
        raise
    _record(
        environment_id=env_id, line_id=line_id, device_id=device_id, ip=ip,
        vendor=adapter.vendor_id, action=action, params=params,
        status="ok" if result.ok else "erro",
        erro=None if result.ok else result.detail, operador=operador,
    )
    return result


async def normalize_environment(
    env_id: str, *, operador: str | None = None,
) -> tuple[str, int]:
    """Dispara a normalização (volume máx + DND off) de todos os telefones com
    IP do ambiente, em background. Devolve ``(run_id, total)``.

    Progresso ao vivo via ``action_state.get(run_id)`` (mesmo padrão do apply);
    rastro persistente em ``device_action_events`` (1 evento por telefone).
    Só roda em ambientes cujo vendor homologou ``normalize``; telefones sem IP
    são pulados. Concorrência limitada.
    """
    with session_factory() as db:
        env = repo.get_environment(db, env_id)
        if env is None:
            raise ValueError("ambiente nao encontrado")
        modelo = env.modelo_telefone
        lines = [
            (ln.id, ln.ip, ln.numero_ramal)
            for ln in repo.list_lines(db, env_id) if ln.ip
        ]
    adapter = adapter_for(modelo)
    if "normalize" not in adapter.capabilities():
        raise VendorActionUnsupported(f"{adapter.vendor_id}: normalize não homologado")

    run_id = uuid.uuid4().hex[:12]
    rs = ActionRunState(run_id=run_id, env_id=env_id, action="normalize")
    for lid, ip, ramal in lines:
        rs.rows.append(ActionRowState(line_id=lid, ip=ip, numero_ramal=ramal))
    action_state.register(rs)
    action_state.prune()

    if not rs.rows:
        rs.finished_at = time.time()
        return run_id, 0

    sem = asyncio.Semaphore(_BULK_CONCURRENCY)

    async def _one(row: ActionRowState) -> None:
        async with sem:
            row.stage = "running"
            row.started_at = time.time()
            try:
                res = await run_action_on_line(
                    row.line_id, "normalize", operador=operador,
                )
                row.stage = "done" if res.ok else "error"
                row.msg = res.detail
                if res.rebooted:
                    row.msg = (row.msg + " " if row.msg else "") + "(telefone reiniciou)"
            except Exception as exc:
                row.stage = "error"
                row.msg = f"{type(exc).__name__}: {exc}"
            finally:
                row.finished_at = time.time()

    async def _worker() -> None:
        await asyncio.gather(*(_one(row) for row in rs.rows))
        rs.finished_at = time.time()
        ok = sum(1 for r in rs.rows if r.stage == "done")
        log.info(
            "normalize_finished", env_id=env_id, run_id=run_id,
            ok=ok, falha=len(rs.rows) - ok, total=len(rs.rows),
            duration_s=round((rs.finished_at or time.time()) - rs.started_at, 2),
        )

    spawn(_worker())
    log.info(
        "normalize_scheduled", env_id=env_id, run_id=run_id,
        total=len(rs.rows), operator=operador,
    )
    return run_id, len(rs.rows)
