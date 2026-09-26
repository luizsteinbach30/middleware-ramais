"""System endpoints: healthz, readyz, version, update controls."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import (
    get_current_user,
    get_session,
    require_admin,
    require_csrf,
)
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import Collection, UpdateHistory, User
from middleware_monitor.core.scheduler import get_scheduler
from middleware_monitor.core.time import iso_utc
from middleware_monitor.domain.config.update_settings import (
    CHANNELS,
    WEEKDAYS,
    UpdateSettings,
    load_update_settings,
    save_update_settings,
)
from middleware_monitor.domain.extension_configurator import repository as ec_repo
from middleware_monitor.jobs import apply_update_schedule
from middleware_monitor.settings import get_settings
from middleware_monitor.updater.service import get_state, run_update_check, set_auto_check
from middleware_monitor.version import __version__

log = get_logger("api.system")

router = APIRouter(prefix="/api/system", tags=["system"])


class HealthOut(BaseModel):
    status: str
    version: str | None = None


class ReadyOut(BaseModel):
    status: str
    reasons: list[str] = []


class VersionOut(BaseModel):
    current: str
    channel: str
    auto_update: bool
    last_check_at: str | None
    last_check_ok: bool
    available_version: str | None
    available_published_at: str | None
    available_notes: str | None
    # True quando `APP_SECRET_KEY` continua no default: sem chave utilizável, as
    # senhas SIP e as senhas web do ambiente seguem gravadas em texto claro. A
    # instalação funciona igual — mas precisa dizer que funciona assim, senão
    # ninguém descobre até alguém abrir o SQLite.
    segredos_em_claro: bool


class UpdateHistoryItem(BaseModel):
    id: int
    timestamp: str
    from_version: str
    to_version: str
    channel: str
    status: str
    error: str | None
    duration_ms: int


@router.get("/healthz", response_model=HealthOut)
def healthz() -> HealthOut:
    # A versão deixa o ajudante do Windows conferir que a release nova subiu (ADR 0008).
    return HealthOut(status="ok", version=__version__)


@router.get("/metrics")
def metrics_endpoint():
    from fastapi import Response

    if not get_settings().metrics_enabled:
        return Response("metrics_disabled", status_code=404)
    from middleware_monitor.core.metrics import render_text

    body, ctype = render_text()
    return Response(body, media_type=ctype)


@router.get("/readyz", response_model=ReadyOut)
def readyz(db: DBSession = Depends(get_session)) -> ReadyOut:
    reasons: list[str] = []
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        reasons.append("db_unreachable")
    try:
        if not get_scheduler().running:
            reasons.append("scheduler_not_running")
    except Exception:
        reasons.append("scheduler_error")
    last = db.scalar(select(Collection).order_by(Collection.collected_at.desc()).limit(1))
    if last is None:
        reasons.append("no_collection_yet")
    return ReadyOut(status="ok" if not reasons else "degraded", reasons=reasons)


@router.get("/version", response_model=VersionOut)
def version_info(
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> VersionOut:
    state = get_state()
    available = state.get("available")
    upd = load_update_settings(db)
    return VersionOut(
        current=__version__,
        channel=upd.channel,
        auto_update=upd.auto_check,
        last_check_at=iso_utc(cast("datetime | None", state.get("last_check_at"))),
        last_check_ok=bool(state.get("last_check_ok")),
        available_version=str(getattr(available, "version", "")) or None,
        available_published_at=getattr(available, "published_at", None),
        available_notes=getattr(available, "notes", None),
        segredos_em_claro=ec_repo.segredos_em_claro(),
    )


class UpdateSettingsIn(BaseModel):
    """Update parcial: só o que vier no body é alterado."""

    auto_check: bool | None = None
    auto_noc: bool | None = None
    channel: str | None = None
    check_hour: int | None = Field(default=None, ge=0, le=23)
    check_minute: int | None = Field(default=None, ge=0, le=59)
    check_days: list[str] | None = None


def _update_settings_out(upd: UpdateSettings) -> dict[str, object]:
    agora = datetime.now().astimezone()
    return {
        **upd.as_dict(),
        # A tela mostra o horário no relógio do servidor; deixamos explícito
        # qual é esse fuso para o operador não precisar adivinhar.
        "timezone": str(agora.tzinfo),
        "timezone_offset": agora.strftime("%z"),
        "weekdays": list(WEEKDAYS),
        "channels": list(CHANNELS),
        # Contrato explícito: o agendamento nunca instala sozinho.
        "installs_automatically": False,
    }


@router.get("/update-settings")
def get_update_settings(
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, object]:
    return _update_settings_out(load_update_settings(db))


@router.put(
    "/update-settings",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def put_update_settings(
    payload: UpdateSettingsIn,
    user: User = Depends(require_admin),
    db: DBSession = Depends(get_session),
) -> dict[str, object]:
    incoming = payload.model_dump(exclude_none=True)
    try:
        upd = save_update_settings(db, incoming, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Vale na hora: reagenda (ou remove) o job sem reiniciar o serviço.
    apply_update_schedule(upd)
    set_auto_check(upd.auto_check)
    log.info(
        "update_settings_saved", operator=user.username, auto_check=upd.auto_check,
        channel=upd.channel, at=f"{upd.check_hour:02d}:{upd.check_minute:02d}",
        days=upd.day_of_week,
    )
    return _update_settings_out(upd)


@router.post(
    "/check-update",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def check_update() -> dict[str, object]:
    release = await run_update_check()
    return {
        "ok": True,
        "available": str(release.version) if release else None,
    }


@router.post(
    "/update",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def apply_update() -> dict[str, object]:
    release = get_state().get("available")
    if release is None:
        # No release cached — try to refresh from GitHub right now so the
        # user doesn't have to click "verificar" first.
        release = await run_update_check()
        if release is None:
            return {"ok": False, "reason": "no_update_available"}
    # O passo a passo mora em updater/instalar.py: é o mesmo da atualização
    # pedida pelo NOC (ADR 0008).
    from middleware_monitor.updater.instalar import FalhaNaInstalacao, instalar

    try:
        return instalar(release)  # type: ignore[arg-type]
    except FalhaNaInstalacao as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/update-history", response_model=list[UpdateHistoryItem])
def history(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> list[UpdateHistoryItem]:
    rows = list(
        db.scalars(
            select(UpdateHistory)
            .order_by(UpdateHistory.timestamp.desc())
            .offset((page - 1) * size)
            .limit(size)
        ).all()
    )
    return [
        UpdateHistoryItem(
            id=r.id,
            timestamp=iso_utc(r.timestamp) or "",
            from_version=r.from_version,
            to_version=r.to_version,
            channel=r.channel,
            status=r.status,
            error=r.error,
            duration_ms=r.duration_ms,
        )
        for r in rows
    ]
