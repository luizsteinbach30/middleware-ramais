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
from typing import Any

import httpx

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import (
    ExtensionApplyRun,
    ExtensionApplyRunLine,
    ExtensionLine,
    LineReapplyEvent,
)
from middleware_monitor.core.tasks import spawn
from middleware_monitor.integrations.extension_configurator.vendors import (
    VendorAdapter,
    VendorAuthError,
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
    line_status,
    pick_lines_to_apply,
)
from .verify import verify_registration_batch, verify_registration_one

log = get_logger("extension_configurator.apply")


async def _ping_host(ip: str, timeout_ms: int = 1500) -> bool:
    if not ip:
        return False
    probe = make_ping_probe()
    latency = await probe.ping(ip, timeout_ms)
    return latency is not None


def build_creds_chain(cfg: dict[str, Any]) -> list[VendorCredentials]:
    """Credenciais a tentar no aparelho, em ordem de preferência.

    * ``[0]`` credencial atual do ambiente (``web_user``/``web_password`` —
      default ``admin``/``admin``).
    * ``[1]`` credencial "nova" (``nova_web_user``/``nova_web_password``),
      incluída só quando configurada e diferente da atual.

    A ordem importa: na primeira aplicação o telefone ainda usa a senha atual;
    a config é que grava a nova. Numa reaplicação o aparelho já pode estar com
    a senha nova — daí o fallback. Tentamos a atual primeiro (cobre o caso
    comum), e a nova como segunda chance quando a atual é recusada.
    """
    web_user = str(cfg.get("web_user") or "admin")
    web_password = str(cfg.get("web_password") or "admin")
    chain = [VendorCredentials(username=web_user, password=web_password)]
    nova_user = str(cfg.get("nova_web_user") or "").strip()
    nova_pwd = str(cfg.get("nova_web_password") or "")
    if nova_user or nova_pwd:
        fallback = VendorCredentials(
            username=nova_user or web_user,
            password=nova_pwd or web_password,
        )
        if (fallback.username, fallback.password) != (web_user, web_password):
            chain.append(fallback)
    return chain


async def _send_config_with_fallback(
    adapter: VendorAdapter,
    ip: str,
    creds_chain: list[VendorCredentials],
    cfg_bytes: bytes,
) -> None:
    """Envia a config tentando cada credencial da chain em ordem.

    Se o aparelho recusar a autenticação (``VendorAuthError`` — 401/403 no HTEK,
    página de login recusada no Intelbras/FlyingVoice) — caso clássico do
    telefone cuja senha já foi trocada para a "nova" numa aplicação anterior —
    tenta a próxima credencial. Qualquer outro erro (rede, 5xx) aborta de
    imediato.
    """
    last_auth_exc: VendorAuthError | None = None
    for idx, creds in enumerate(creds_chain):
        is_last = idx == len(creds_chain) - 1
        try:
            await adapter.send_config(ip, creds, cfg_bytes, fmt="xml")
            if idx > 0:
                log.info("apply_auth_fallback_ok", ip=ip, cred_index=idx)
            return
        except VendorAuthError as exc:
            if not is_last:
                log.info("apply_auth_fallback_try", ip=ip, next_index=idx + 1)
                last_auth_exc = exc
                continue
            raise
    if last_auth_exc is not None:  # defensivo: chain vazia nunca ocorre
        raise last_auth_exc


async def _apply_row(
    row: RowState,
    *,
    env_id: str,
    adapter: VendorAdapter,
    cfg_bytes: bytes,
    creds_chain: list[VendorCredentials],
    validar_conectividade: bool,
    run_line_id: int | None = None,
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
            await _send_config_with_fallback(adapter, row.ip, creds_chain, cfg_bytes)
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
        if line is not None:
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
        # Fecha o snapshot do relatório (status_depois + erro), mesmo se a linha
        # foi removida nesse meio-tempo.
        if run_line_id is not None:
            rl = db.get(ExtensionApplyRunLine, run_line_id)
            if rl is not None:
                repo.finish_run_line(
                    db, rl,
                    status_depois=status_final,
                    erro=err,
                    modelo=line.ultimo_modelo if line is not None else None,
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
        creds_chain = build_creds_chain(cfg)
        validar = bool(cfg.get("validar_conectividade", True))
        verificar = bool(cfg.get("verificar_registro_sip", False))
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
        # Snapshot por linha impactada (status ANTES de aplicar).
        run_line_ids: dict[str, int] = {}
        for line, hash_esperado in targets:
            rl = repo.create_run_line(
                db,
                run_id=db_run_id,
                line_id=line.id,
                numero_ramal=line.numero_ramal,
                ip=line.ip,
                nome_visivel=line.nome_visivel,
                status_antes=line_status(line, hash_esperado),
            )
            run_line_ids[line.id] = rl.id
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
                creds_chain=creds_chain,
                validar_conectividade=validar,
                run_line_id=run_line_ids.get(rs.rows[idx].line_id),
            )))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Verificação de registro SIP (opt-in): UMA consulta cobre todos os ramais
        # que enviaram OK — não dispara 1 request por ramal. Roda antes de marcar
        # finished_at para o run aparecer "em andamento" durante a checagem.
        if verificar:
            ok_rows = [
                (r.numero_ramal, run_line_ids.get(r.line_id))
                for r in rs.rows if r.stage == "done"
            ]
            ramais = [ramal for ramal, _rl in ok_rows if ramal]
            res = await verify_registration_batch(session_factory, ramais) if ramais else None
            if res is not None:
                with session_factory() as db:
                    for ramal, rl_id in ok_rows:
                        if rl_id is None:
                            continue
                        rl = db.get(ExtensionApplyRunLine, rl_id)
                        if rl is not None:
                            rl.registro_sip = res.get(ramal, "unregistered") if ramal else "skipped"
                    db.commit()

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
        creds_chain = build_creds_chain(cfg)
        validar = bool(cfg.get("validar_conectividade", True))
        verificar = bool(cfg.get("verificar_registro_sip", False))
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
        rl = repo.create_run_line(
            db,
            run_id=db_run.id,
            line_id=line.id,
            numero_ramal=line.numero_ramal,
            ip=line.ip,
            nome_visivel=line.nome_visivel,
            status_antes=line_status(line, hash_esperado),
        )
        db_run_id = db_run.id
        event_id = ev.id
        run_line_id = rl.id
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

    async def _single_worker() -> None:
        await _apply_row(
            rs.rows[0],
            env_id=env.id,
            adapter=adapter,
            cfg_bytes=payload,
            creds_chain=creds_chain,
            validar_conectividade=validar,
            run_line_id=run_line_id,
        )
        # Verificação de registro SIP (opt-in): consulta só este ramal.
        registro: str | None = None
        if verificar and rs.rows[0].stage == "done":
            registro = await verify_registration_one(session_factory, line_ramal)

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
            if registro is not None:
                rl_obj = db.get(ExtensionApplyRunLine, run_line_id)
                if rl_obj is not None:
                    rl_obj.registro_sip = registro
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
