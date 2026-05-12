"""System endpoints: healthz, readyz, version, update controls."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import (
    get_current_user,
    get_session,
    require_admin,
    require_csrf,
)
from middleware_monitor.core.models import Collection, UpdateHistory, User
from middleware_monitor.core.scheduler import get_scheduler
from middleware_monitor.core.time import iso_utc
from middleware_monitor.settings import get_settings
from middleware_monitor.updater.installer import install_release
from middleware_monitor.updater.service import get_state, run_update_check
from middleware_monitor.version import __version__

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
def metrics_endpoint():  # noqa: ANN201
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
    except Exception:  # noqa: BLE001
        reasons.append("db_unreachable")
    try:
        if not get_scheduler().running:
            reasons.append("scheduler_not_running")
    except Exception:  # noqa: BLE001
        reasons.append("scheduler_error")
    last = db.scalar(select(Collection).order_by(Collection.collected_at.desc()).limit(1))
    if last is None:
        reasons.append("no_collection_yet")
    return ReadyOut(status="ok" if not reasons else "degraded", reasons=reasons)


@router.get("/version", response_model=VersionOut)
def version_info(_user: User = Depends(get_current_user)) -> VersionOut:
    settings = get_settings()
    state = get_state()
    available = state.get("available")
    return VersionOut(
        current=__version__,
        channel=settings.update_channel,
        auto_update=bool(state.get("auto_update", True)),
        last_check_at=iso_utc(state.get("last_check_at")),
        last_check_ok=bool(state.get("last_check_ok")),
        available_version=str(getattr(available, "version", "")) or None,
        available_published_at=getattr(available, "published_at", None),
        available_notes=getattr(available, "notes", None),
    )


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
    release = (get_state().get("available"))
    if release is None:
        return {"ok": False, "reason": "no_update_available"}
    asyncio.create_task(install_release(release))  # type: ignore[arg-type]
    return {"ok": True, "started_for": str(getattr(release, "version", ""))}


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
