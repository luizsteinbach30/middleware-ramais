"""Pipeline de aplicacao em massa do Configurador de Ramais.

Padrao do middleware:
  - cada sessao DB e' aberta uma vez por chamada de top-level (`run_apply`)
  - subprocess de ping reusa `make_ping_probe()` (cross-platform OS-aware)
  - logs via structlog
  - estado in-memory para tracking ao vivo (`run_state._RUNS`)
  - background via `core.tasks.spawn`

Regras herdadas do autocfg-ramais:
  - pipeline minimalista: ping (opcional) -> send. SEM discover/fingerprint
    automatico (feedback-nao-bater-no-aparelho)
  - rolling delay (default 1s) entre disparos para evitar pico de rede
  - validar_conectividade (default True) — desativa em redes sem ICMP
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import (
    ExtensionApplyRun,
    ExtensionLine,
    LineReapplyEvent,
)
from middleware_monitor.core.tasks import spawn
from middleware_monitor.integrations.extension_configurator.vendors import (
    VendorAdapter,
    VendorCredentials,
)
from middleware_monitor.integrations.network import make_ping_probe

from . import repository as repo
from . import run_state
from .run_state import RowState, RunState
from .service import (
    adapter_for,
    build_row,
    build_template,
    compute_line_hash,
    pick_lines_to_apply,
)

log = get_logger("extension_configurator.apply")


async def _ping_host(ip: str, timeout_ms: int = 1500) -> bool:
    if not ip:
        return False
    probe = make_ping_probe()
    latency = await probe.ping(ip, timeout_ms)
    return latency is not None


async def _apply_row(
    row: RowState,
    *,
    env_id: str,
    adapter: VendorAdapter,
    cfg_bytes: bytes,
    creds: VendorCredentials,
    validar_conectividade: bool,
) -> None:
    row.started_at = time.time()
    err: str | None = None
    status_final = "ok"
    try:
        if validar_conectividade:
            row.stage = "ping"
            if not await _ping_host(row.ip):
                raise RuntimeError(
                    "host nao responde ao ping (offline ou ICMP bloqueado). "
                    "Se a rede bloqueia ICMP, desative 'Validar conectividade' "
                    "na config do ambiente.",
                )
        row.stage = "send"
        try:
            await adapter.send_config(row.ip, creds, cfg_bytes, fmt="xml")
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"send falhou — {type(exc).__name__}: {exc}",
            ) from exc
        row.stage = "done"
        row.msg = "config aplicada"
    except Exception as exc:  # sempre persiste como erro
        # ruff: noqa
        status_final = "erro"
        err = str(exc) if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}"
        row.stage = "error"
        row.msg = err
        log.warning(
            "apply_row_failed", env_id=env_id, line_id=row.line_id, error=err,
        )
    finally:
        row.finished_at = time.time()

    # Sessao curta — atualiza e fecha. Evita segurar transacao durante I/O.
    # Em caso de sucesso, RECALCULA o hash com env+linha fresh do DB para
    # garantir match com o `compute_line_hash` do reload (evita "outdated"
    # falso causado por qualquer divergencia tipo cache/serializacao).
    with session_factory() as db:
        line = db.get(ExtensionLine, row.line_id)
        if line is None:
            return
        final_hash: str | None = None
        if status_final == "ok":
            env = repo.get_environment(db, env_id)
            final_hash = (
                compute_line_hash(env, line) if env is not None else row.hash_esperado
            )
        repo.update_line_status(
            db, line,
            status=status_final,
            erro=err,
            hash_aplicado=final_hash,
        )
        db.commit()


async def run_apply(
    env_id: str,
    *,
    force: bool,
    selected_ids: list[str] | None,
    operador: str | None,
    rolling_delay_ms: int = 1000,
) -> tuple[str, int]:
    """Cria o run no DB + memoria, dispara worker em background e devolve
    `(run_id, total)`. Caller pode acompanhar via `run_state.get(run_id)`.

    Levanta `ValueError` se o ambiente nao existe.
    """
    with session_factory() as db:
        env = repo.get_environment(db, env_id)
        if env is None:
            raise ValueError(f"ambiente '{env_id}' nao existe")
        cfg = repo.merged_config_padrao(env)
        lines = repo.list_lines(db, env_id)
        targets = pick_lines_to_apply(
            env, lines, force=force, selected_ids=selected_ids,
        )
        web_user = str(cfg.get("web_user") or "admin")
        web_password = str(cfg.get("web_password") or "admin")
        validar = bool(cfg.get("validar_conectividade", True))
        adapter = adapter_for(env.modelo_telefone)
        template = build_template(cfg)

        # Pre-gera os cfg_bytes ainda dentro da sessao (precisa de env+line).
        prepared: list[tuple[str, str, str, str, bytes]] = []
        for line, hash_esperado in targets:
            payload = adapter.generate_config(template, build_row(line, cfg))
            prepared.append((line.id, line.ip, line.numero_ramal, hash_esperado, payload))

        db_run = repo.create_run(
            db, env_id, total=len(prepared), forcado=force, operador=operador,
        )
        db_run_id = db_run.id
        db.commit()

    run_id = uuid.uuid4().hex[:12]

    if not prepared:
        with session_factory() as db:
            empty_run = db.get(ExtensionApplyRun, db_run_id)
            if empty_run is not None:
                repo.finish_run(db, empty_run, ok=0, falha=0)
                db.commit()
        rs = RunState(run_id=run_id, env_id=env_id, db_run_id=db_run_id)
        rs.finished_at = time.time()
        run_state.register(rs)
        return run_id, 0

    rs = RunState(run_id=run_id, env_id=env_id, db_run_id=db_run_id)
    for line_id, ip, ramal, hash_esperado, _payload in prepared:
        rs.rows.append(RowState(
            line_id=line_id, ip=ip, numero_ramal=ramal, hash_esperado=hash_esperado,
        ))
    run_state.register(rs)
    run_state.prune()

    creds = VendorCredentials(username=web_user, password=web_password)

    async def _worker() -> None:
        delay_s = max(0, rolling_delay_ms) / 1000.0
        tasks: list[asyncio.Task[None]] = []
        for idx, prep in enumerate(prepared):
            payload = prep[4]
            if idx > 0 and delay_s > 0:
                await asyncio.sleep(delay_s)
            if rs.cancelled:
                row = rs.rows[idx]
                if row.stage == "pending":
                    row.stage = "error"
                    row.msg = "cancelado antes do inicio"
                    row.started_at = row.started_at or time.time()
                    row.finished_at = time.time()
                continue
            tasks.append(asyncio.create_task(_apply_row(
                rs.rows[idx],
                env_id=env_id,
                adapter=adapter,
                cfg_bytes=payload,
                creds=creds,
                validar_conectividade=validar,
            )))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        rs.finished_at = time.time()
        ok = sum(1 for r in rs.rows if r.stage == "done")
        falha = sum(1 for r in rs.rows if r.stage == "error")
        with session_factory() as db:
            db_run_obj = db.get(ExtensionApplyRun, db_run_id)
            if db_run_obj is not None:
                repo.finish_run(db, db_run_obj, ok=ok, falha=falha)
                db.commit()
        log.info(
            "apply_finished", env_id=env_id, run_id=run_id, db_run_id=db_run_id,
            ok=ok, falha=falha, total=len(rs.rows),
            duration_s=round((rs.finished_at or time.time()) - rs.started_at, 2),
        )

    spawn(_worker())
    log.info(
        "apply_scheduled", env_id=env_id, run_id=run_id, db_run_id=db_run_id,
        total=len(rs.rows), force=force, validar_conectividade=validar,
        operator=operador,
    )
    return run_id, len(rs.rows)


async def run_apply_single_line(
    line_id: str,
    *,
    operador: str,
    reason: str,
) -> tuple[str, int, int]:
    """Aplica config de UMA linha (ad-hoc).

    Cria ExtensionApplyRun(total=1) + LineReapplyEvent (status='running').
    Dispara worker em background que atualiza ambos ao final.

    `reason`: 'recovery' (watcher) | 'manual_device_page' (tela Devices).
    Retorna `(run_id, db_run_id, event_id)`.
    Levanta `ValueError` se a linha não existe ou não tem IP/ambiente.
    """
    with session_factory() as db:
        line = repo.get_line(db, line_id)
        if line is None:
            raise ValueError(f"linha '{line_id}' nao existe")
        if not line.ip:
            raise ValueError(f"linha '{line_id}' sem IP — nada a aplicar")
        env = repo.get_environment(db, line.environment_id)
        if env is None:
            raise ValueError(f"ambiente '{line.environment_id}' nao existe")

        cfg = repo.merged_config_padrao(env)
        web_user = str(cfg.get("web_user") or "admin")
        web_password = str(cfg.get("web_password") or "admin")
        validar = bool(cfg.get("validar_conectividade", True))
        adapter = adapter_for(env.modelo_telefone)
        template = build_template(cfg)
        payload = adapter.generate_config(template, build_row(line, cfg))
        hash_esperado = compute_line_hash(env, line)

        db_run = repo.create_run(
            db, env.id, total=1, forcado=True, operador=operador,
        )
        ev = repo.create_reapply_event(
            db,
            line_id=line.id,
            device_id=line.device_id,
            reason=reason,
            run_id=db_run.id,
        )
        db_run_id = db_run.id
        event_id = ev.id
        line_ip = line.ip
        line_ramal = line.numero_ramal
        db.commit()

    run_id = uuid.uuid4().hex[:12]
    rs = RunState(run_id=run_id, env_id=env.id, db_run_id=db_run_id)
    rs.rows.append(RowState(
        line_id=line.id, ip=line_ip, numero_ramal=line_ramal,
        hash_esperado=hash_esperado,
    ))
    run_state.register(rs)
    run_state.prune()
    creds = VendorCredentials(username=web_user, password=web_password)

    async def _single_worker() -> None:
        await _apply_row(
            rs.rows[0],
            env_id=env.id,
            adapter=adapter,
            cfg_bytes=payload,
            creds=creds,
            validar_conectividade=validar,
        )
        rs.finished_at = time.time()
        ok = 1 if rs.rows[0].stage == "done" else 0
        falha = 1 if rs.rows[0].stage == "error" else 0
        ev_status = "ok" if ok else "erro"
        ev_error = rs.rows[0].msg if falha else None
        with session_factory() as db:
            db_run_obj = db.get(ExtensionApplyRun, db_run_id)
            if db_run_obj is not None:
                repo.finish_run(db, db_run_obj, ok=ok, falha=falha)
            ev_obj = db.get(LineReapplyEvent, event_id)
            if ev_obj is not None:
                repo.finish_reapply_event(
                    db, ev_obj, status=ev_status, error=ev_error,
                )
            db.commit()
        log.info(
            "apply_single_finished", line_id=line_id, run_id=run_id,
            db_run_id=db_run_id, event_id=event_id,
            status=ev_status, reason=reason, operador=operador,
        )

    spawn(_single_worker())
    log.info(
        "apply_single_scheduled", line_id=line_id, run_id=run_id,
        db_run_id=db_run_id, event_id=event_id, reason=reason, operador=operador,
    )
    return run_id, db_run_id, event_id
