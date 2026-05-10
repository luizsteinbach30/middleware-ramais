"""Endpoints to read and update the application configuration."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import get_session, require_admin, require_csrf
from middleware_monitor.core.models import User
from middleware_monitor.domain.config.repository import (
    load_config,
    update_config,
)
from middleware_monitor.domain.config.schemas import (
    AppConfigOut,
    AppConfigUpdate,
    UscallTestRequest,
    UscallTestResponse,
)
from middleware_monitor.integrations.uscall_client import UscallClient

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config", response_model=AppConfigOut, dependencies=[Depends(require_admin)])
def get_app_config(db: DBSession = Depends(get_session)) -> AppConfigOut:
    return load_config(db)


@router.put(
    "/config",
    response_model=AppConfigOut,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def put_app_config(
    payload: AppConfigUpdate,
    user: User = Depends(require_admin),
    db: DBSession = Depends(get_session),
) -> AppConfigOut:
    return update_config(db, payload, user_id=user.id)


@router.post(
    "/uscall/test",
    response_model=UscallTestResponse,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def test_uscall(
    payload: UscallTestRequest,
    db: DBSession = Depends(get_session),
) -> UscallTestResponse:
    cfg = load_config(db)
    host = (payload.host or cfg.uscall_host or "").strip()
    if not host:
        return UscallTestResponse(success=False, error="missing_host")
    if payload.token:
        token = payload.token
    elif cfg.uscall_token == "set":
        from middleware_monitor.domain.config.repository import load_secret

        token = load_secret(db, "uscall_token") or ""
    else:
        return UscallTestResponse(success=False, error="missing_token")
    client = UscallClient(host=host, token=token, verify_ssl=cfg.uscall_verify_ssl)
    result = await client.test()
    return UscallTestResponse(
        success=result.success,
        http_status=result.http_status,
        latency_ms=result.latency_ms,
        error=result.error,
    )
