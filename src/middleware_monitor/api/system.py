"""System endpoints: healthz, readyz, version, update controls."""

from __future__ import annotations

import sys
import threading
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
from middleware_monitor.core.tasks import spawn
from middleware_monitor.core.time import iso_utc
from middleware_monitor.domain.config.update_settings import (
    CHANNELS,
    WEEKDAYS,
    UpdateSettings,
    load_update_settings,
    save_update_settings,
)
from middleware_monitor.jobs import apply_update_schedule
from middleware_monitor.settings import get_settings
from middleware_monitor.updater.installer import install_release
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
    return HealthOut(status="ok")


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
    )


class UpdateSettingsIn(BaseModel):
    """Update parcial: só o que vier no body é alterado."""

    auto_check: bool | None = None
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

    # Standalone single-exe path: this is the ONLY path that works for the
    # PyInstaller build delivered as MiddlewareMonitor-X.Y.Z.exe. Any
    # tarball-based install (installer.py) assumes a venv + service and
    # silently fails inside the .exe.
    if getattr(sys, "frozen", False):
        from middleware_monitor.desktop import get_data_dir, request_shutdown
        from middleware_monitor.updater.standalone import (
            UpdateError,
            apply_standalone_update,
            find_exe_asset,
        )

        asset = find_exe_asset(getattr(release, "assets", []))
        if asset is None:
            raise HTTPException(
                status_code=500,
                detail="no_exe_asset_in_release",
            )
        sha = getattr(release, "sha256sums", None)
        sha_url = (getattr(sha, "api_url", "") or getattr(sha, "download_url", "")) if sha else None
        try:
            apply_standalone_update(
                asset_url=asset["url"],
                asset_name=asset["name"],
                data_dir=get_data_dir(),
                sha_url=sha_url,
                token=get_settings().effective_update_token,
            )
        except (UpdateError, OSError) as exc:
            log.error("standalone_update_failed", error=str(exc))
            raise HTTPException(status_code=500, detail=f"download_failed: {exc}") from exc

        # Helper batch is now waiting for our PID to die. Give the HTTP
        # response a moment to flush, then signal the Tk main loop to
        # tear everything down. The helper picks up the swap and relaunches.
        log.info("standalone_update_scheduled", version=str(getattr(release, "version", "")))
        threading.Timer(1.0, request_shutdown).start()
        return {
            "ok": True,
            "mode": "standalone",
            "started_for": str(getattr(release, "version", "")),
            "shutdown_in_seconds": 1,
        }

    # Legacy tarball-based path (kept for non-frozen installations that
    # ship with their own service supervisor).
    spawn(install_release(release))  # type: ignore[arg-type]
    return {"ok": True, "mode": "legacy", "started_for": str(getattr(release, "version", ""))}


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
