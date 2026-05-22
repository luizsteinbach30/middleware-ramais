"""Endpoints do Configurador de Ramais.

Padroes do projeto:
  - auth obrigatoria em GET (`get_current_user`)
  - CSRF + admin em mutacoes (`require_csrf`, `require_admin`)
  - Pydantic in/out, structlog, sem logica de dominio aqui (so orquestracao)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import (
    get_current_user,
    get_session,
    require_admin,
    require_csrf,
)
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import (
    ExtensionApplyRun,
    ExtensionEnvironment,
    ExtensionLine,
    User,
)
from middleware_monitor.core.time import iso_utc
from middleware_monitor.domain.extension_configurator import (
    apply as apply_mod,
)
from middleware_monitor.domain.extension_configurator import (
    repository as repo,
)
from middleware_monitor.domain.extension_configurator import (
    run_state,
)
from middleware_monitor.domain.extension_configurator.defaults import PHONE_MODELS
from middleware_monitor.domain.extension_configurator.service import compute_statuses

router = APIRouter(prefix="/api/extension-configurator", tags=["extension-configurator"])
log = get_logger("api.extension_configurator")


def _status_resumo(lines: list[ExtensionLine]) -> dict[str, Any]:
    """Agrega o status das linhas do ambiente em 1 categoria + contadores."""
    if not lines:
        return {"applied": 0, "pending": 0, "error": 0, "agregado": "vazio"}
    applied = sum(1 for ln in lines if ln.ultimo_status == "applied")
    error = sum(1 for ln in lines if ln.ultimo_status == "error")
    pending = len(lines) - applied - error
    if error > 0:
        agregado = "erros"
    elif pending > 0:
        agregado = "pendentes"
    else:
        agregado = "ok"
    return {
        "applied": applied, "pending": pending, "error": error,
        "agregado": agregado,
    }


def _searchable_text(
    env: ExtensionEnvironment, lines: list[ExtensionLine],
) -> str:
    """Texto pre-concatenado pra busca livre client-side (lowercase)."""
    bits: list[str] = [env.nome or "", env.modelo_telefone or ""]
    for ln in lines:
        bits.extend([
            ln.numero_ramal or "",
            ln.ip or "",
            ln.nome_visivel or "",
            ln.user_auth or "",
            ln.numero_abreviado or "",
            ln.ultimo_mac or "",
            ln.ultimo_modelo or "",
        ])
    return " ".join(b for b in bits if b).lower()


def _env_summary(
    env: ExtensionEnvironment, lines: list[ExtensionLine],
) -> dict[str, Any]:
    runs = sorted(env.runs, key=lambda r: r.started_at, reverse=True)
    last = runs[0] if runs else None
    return {
        "id": env.id,
        "nome": env.nome,
        "modelo_telefone": env.modelo_telefone,
        "telefones": len(lines),
        "atualizado_em": iso_utc(env.updated_at),
        "ultima_execucao": _run_dict(last) if last else None,
        "status_resumo": _status_resumo(lines),
        "searchable": _searchable_text(env, lines),
    }


def _line_dict(line: ExtensionLine, status: dict[str, str]) -> dict[str, Any]:
    return {
        "id": line.id,
        "ip": line.ip,
        "numero_ramal": line.numero_ramal,
        "user_auth": line.user_auth,
        "senha_sip": line.senha_sip,
        "servidor_sip": line.servidor_sip,
        "numero_abreviado": line.numero_abreviado,
        "nome_visivel": line.nome_visivel,
        "ultimo_hash_aplicado": line.ultimo_hash_aplicado,
        "ultimo_status": line.ultimo_status,
        "ultima_aplicacao": iso_utc(line.ultima_aplicacao),
        "ultimo_erro": line.ultimo_erro,
        "ultimo_modelo": line.ultimo_modelo,
        "ultimo_mac": line.ultimo_mac,
        "status": status.get("status", "pending"),
        "hash_atual": status.get("hash_atual", ""),
    }


def _run_dict(run: ExtensionApplyRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "environment_id": run.environment_id,
        "started_at": iso_utc(run.started_at),
        "finished_at": iso_utc(run.finished_at),
        "total": run.total,
        "ok": run.ok,
        "falha": run.falha,
        "forcado": run.forcado,
        "operador": run.operador,
    }


# --------------------------------------------------------------------- Models


class EnvironmentCreateIn(BaseModel):
    nome: str
    modelo_telefone: str = "HTEK UC902G"


class EnvironmentUpdateIn(BaseModel):
    nome: str | None = None
    config_padrao: dict[str, Any] | None = None


class LinesIn(BaseModel):
    linhas: list[dict[str, Any]]


class ApplyIn(BaseModel):
    selected_ids: list[str] | None = None


# --------------------------------------------------------------------- Routes


@router.get("/phone-models")
def phone_models(_user: User = Depends(get_current_user)) -> dict[str, list[str]]:
    return {"models": PHONE_MODELS}


@router.get("/environments")
def list_environments(
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    envs = repo.list_environments(db)
    out = [_env_summary(e, repo.list_lines(db, e.id)) for e in envs]
    return {"environments": out}


@router.post(
    "/environments",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def create_environment(
    payload: EnvironmentCreateIn,
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    nome = payload.nome.strip()
    modelo = payload.modelo_telefone.strip() or "HTEK UC902G"
    if not nome:
        raise HTTPException(400, "nome obrigatorio")
    env = repo.create_environment(db, nome=nome, modelo_telefone=modelo)
    db.commit()
    log.info("environment_created", env_id=env.id, modelo=modelo)
    return _env_summary(env, [])


@router.get("/environments/{env_id}")
def environment_detail(
    env_id: str,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    env = repo.get_environment(db, env_id)
    if env is None:
        raise HTTPException(404, "ambiente nao encontrado")
    lines = repo.list_lines(db, env_id)
    statuses = {s["id"]: s for s in compute_statuses(env, lines)}
    return {
        "id": env.id,
        "nome": env.nome,
        "modelo_telefone": env.modelo_telefone,
        "config_padrao": repo.merged_config_padrao(env),
        "criado_em": iso_utc(env.created_at),
        "atualizado_em": iso_utc(env.updated_at),
        "linhas": [_line_dict(ln, statuses.get(ln.id, {})) for ln in lines],
    }


@router.put(
    "/environments/{env_id}",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def update_environment(
    env_id: str,
    payload: EnvironmentUpdateIn,
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    env = repo.get_environment(db, env_id)
    if env is None:
        raise HTTPException(404, "ambiente nao encontrado")
    repo.update_environment(
        db, env, nome=payload.nome, config_padrao=payload.config_padrao,
    )
    db.commit()
    return _env_summary(env, repo.list_lines(db, env_id))


@router.delete(
    "/environments/{env_id}",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def delete_environment(
    env_id: str, db: DBSession = Depends(get_session),
) -> dict[str, str]:
    if not repo.delete_environment(db, env_id):
        raise HTTPException(404, "ambiente nao encontrado")
    db.commit()
    log.info("environment_deleted", env_id=env_id)
    return {"deleted": env_id}


@router.put(
    "/environments/{env_id}/lines",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def save_lines(
    env_id: str,
    payload: LinesIn,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    env = repo.get_environment(db, env_id)
    if env is None:
        raise HTTPException(404, "ambiente nao encontrado")
    repo.save_lines(db, env, payload.linhas)
    db.commit()
    return environment_detail(env_id, _user=_user, db=db)


@router.post(
    "/environments/{env_id}/apply",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def apply_environment(
    env_id: str,
    force: bool = Query(default=False),
    rolling_delay_ms: int = Query(default=1000, ge=0, le=60000),
    payload: ApplyIn = Body(default_factory=ApplyIn),
    user: User = Depends(require_admin),
) -> dict[str, Any]:
    selected = payload.selected_ids or None
    try:
        run_id, total = await apply_mod.run_apply(
            env_id,
            force=force,
            selected_ids=selected,
            operador=user.username,
            rolling_delay_ms=rolling_delay_ms,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"run_id": run_id, "total": total}


@router.get("/environments/{env_id}/runs")
def list_runs(
    env_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    runs = repo.list_runs(db, env_id=env_id, limit=limit)
    return {"runs": [_run_dict(r) for r in runs]}


@router.get("/runs")
def list_all_runs(
    limit: int = Query(default=100, ge=1, le=500),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    runs = repo.list_runs(db, limit=limit)
    return {"runs": [_run_dict(r) for r in runs]}


@router.get("/runs/{run_id}/detail")
def run_detail(
    run_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, Any]:
    """Detalhe de um run gravado no DB.

    Estrategia (mesma do autocfg-ramais): o storage so guarda totais por run,
    nao snapshot por linha. Apresentamos o estado ATUAL das linhas do ambiente
    como aproximacao — eh o que ficou apos a ultima execucao. Para acompanhar
    um run em curso, use `/live`.
    """
    db_run = db.get(ExtensionApplyRun, run_id)
    if db_run is None:
        raise HTTPException(404, "run nao encontrado")
    env = repo.get_environment(db, db_run.environment_id)
    lines = repo.list_lines(db, db_run.environment_id) if env else []
    statuses = (
        {s["id"]: s for s in compute_statuses(env, lines)} if env else {}
    )
    return {
        "run": _run_dict(db_run),
        "environment": {
            "id": env.id, "nome": env.nome, "modelo_telefone": env.modelo_telefone,
        } if env else None,
        "linhas": [_line_dict(ln, statuses.get(ln.id, {})) for ln in lines],
    }


@router.get("/runs/{run_id}/live")
def live_status(
    run_id: str, _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    rs = run_state.get(run_id)
    if rs is None:
        raise HTTPException(404, "run nao encontrado (pode ter expirado da memoria)")
    return {
        "run_id": rs.run_id,
        "env_id": rs.env_id,
        "db_run_id": rs.db_run_id,
        "started_at": rs.started_at,
        "finished_at": rs.finished_at,
        "cancelled": rs.cancelled,
        "summary": rs.summary(),
        "rows": [
            {
                "line_id": r.line_id,
                "ip": r.ip,
                "numero_ramal": r.numero_ramal,
                "stage": r.stage,
                "msg": r.msg,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
            }
            for r in rs.rows
        ],
    }


@router.post(
    "/runs/{run_id}/cancel",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def cancel_run(run_id: str) -> dict[str, bool]:
    cancelled = run_state.cancel(run_id)
    if not cancelled:
        raise HTTPException(409, "run ja finalizado ou inexistente")
    return {"cancelled": True}
