"""Endpoints to read and update the application configuration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import get_session, require_admin, require_csrf
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import UscallServer, User
from middleware_monitor.domain.config.repository import (
    load_config,
    update_config,
)
from middleware_monitor.domain.config.schemas import (
    AppConfigOut,
    AppConfigUpdate,
    UscallServerIn,
    UscallServerOut,
    UscallTestRequest,
    UscallTestResponse,
)
from middleware_monitor.domain.uscall import repository as uscall_repo
from middleware_monitor.integrations.uscall_client import UscallClient

router = APIRouter(prefix="/api", tags=["config"])
log = get_logger("api.config")


def _server_out(srv: UscallServer) -> UscallServerOut:
    return UscallServerOut(
        id=srv.id,
        nome=srv.nome,
        host=srv.host,
        token="set" if srv.token else None,
        verify_ssl=srv.verify_ssl,
        enabled=srv.enabled,
    )


def _with_servers(db: DBSession, cfg: AppConfigOut) -> AppConfigOut:
    cfg.uscall_servers = [_server_out(s) for s in uscall_repo.list_servers(db)]
    return cfg


@router.get("/config", response_model=AppConfigOut, dependencies=[Depends(require_admin)])
def get_app_config(db: DBSession = Depends(get_session)) -> AppConfigOut:
    return _with_servers(db, load_config(db))


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
    return _with_servers(db, update_config(db, payload, user_id=user.id))


# ------------------------------------------------------ USCall servers (CRUD)


@router.get(
    "/config/uscall-servers",
    response_model=list[UscallServerOut],
    dependencies=[Depends(require_admin)],
)
def list_uscall_servers(db: DBSession = Depends(get_session)) -> list[UscallServerOut]:
    return [_server_out(s) for s in uscall_repo.list_servers(db)]


@router.post(
    "/config/uscall-servers",
    response_model=UscallServerOut,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def create_uscall_server(
    payload: UscallServerIn,
    db: DBSession = Depends(get_session),
) -> UscallServerOut:
    # Na criação o token é obrigatório de verdade ("set" só faz sentido na edição).
    if not payload.token or payload.token == "set":
        raise HTTPException(422, "token_obrigatorio")
    srv = uscall_repo.create_server(
        db,
        nome=payload.nome,
        host=payload.host,
        token_plain=payload.token,
        verify_ssl=payload.verify_ssl,
        enabled=payload.enabled,
    )
    db.commit()
    log.info("uscall_server_created", server_id=srv.id, nome=srv.nome, host=srv.host)
    return _server_out(srv)


@router.put(
    "/config/uscall-servers/{server_id}",
    response_model=UscallServerOut,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def update_uscall_server(
    server_id: int,
    payload: UscallServerIn,
    db: DBSession = Depends(get_session),
) -> UscallServerOut:
    srv = uscall_repo.get_server(db, server_id)
    if srv is None:
        raise HTTPException(404, "servidor nao encontrado")
    if payload.token == "":
        raise HTTPException(422, "token_obrigatorio")
    # token None ou "set" → mantém o token cifrado atual
    token_plain = None if payload.token in (None, "set") else payload.token
    uscall_repo.update_server(
        db,
        srv,
        nome=payload.nome,
        host=payload.host,
        token_plain=token_plain,
        verify_ssl=payload.verify_ssl,
        enabled=payload.enabled,
    )
    db.commit()
    log.info("uscall_server_updated", server_id=srv.id, nome=srv.nome, host=srv.host)
    return _server_out(srv)


@router.delete(
    "/config/uscall-servers/{server_id}",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def delete_uscall_server(
    server_id: int,
    db: DBSession = Depends(get_session),
) -> dict[str, bool]:
    if not uscall_repo.delete_server(db, server_id):
        raise HTTPException(404, "servidor nao encontrado")
    db.commit()
    log.info("uscall_server_deleted", server_id=server_id)
    return {"ok": True}


# ------------------------------------------------------------- teste USCall


@router.post(
    "/uscall/test",
    response_model=UscallTestResponse,
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def test_uscall(
    payload: UscallTestRequest,
    db: DBSession = Depends(get_session),
) -> UscallTestResponse:
    if payload.server_id is not None:
        srv = uscall_repo.get_server(db, payload.server_id)
        if srv is None:
            return UscallTestResponse(success=False, error="server_not_found")
        # form pode sobrepor host/token não salvos durante uma edição
        host = (payload.host or srv.host or "").strip()
        token = payload.token or uscall_repo.load_server_token(srv) or ""
        verify_ssl = payload.verify_ssl if payload.verify_ssl is not None else srv.verify_ssl
    else:
        host = (payload.host or "").strip()
        token = payload.token or ""
        verify_ssl = payload.verify_ssl if payload.verify_ssl is not None else True
    if not host:
        return UscallTestResponse(success=False, error="missing_host")
    if not token:
        return UscallTestResponse(success=False, error="missing_token")
    client = UscallClient(host=host, token=token, verify_ssl=verify_ssl)
    result = await client.test()
    return UscallTestResponse(
        success=result.success,
        http_status=result.http_status,
        latency_ms=result.latency_ms,
        error=result.error,
    )
