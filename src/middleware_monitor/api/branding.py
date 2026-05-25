"""Endpoints da identidade visual (logo + favicon).

GET dos assets é **público** (a tela de login e o favicon precisam carregar sem
sessão). Upload/remoção exigem CSRF + admin.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from middleware_monitor import branding
from middleware_monitor.api.deps import (
    get_current_user,
    require_admin,
    require_csrf,
)
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import User

router = APIRouter(prefix="/api/branding", tags=["branding"])
log = get_logger("api.branding")


def _validate_kind(kind: str) -> None:
    if kind not in branding.KINDS:
        raise HTTPException(status_code=404, detail="not_found")


@router.get("/status")
def status(_user: User = Depends(get_current_user)) -> dict[str, bool]:
    return {k: branding.find_asset(k) is not None for k in branding.KINDS}


@router.get("/{kind}")
def get_asset(kind: str) -> Response:
    _validate_kind(kind)
    path = branding.find_asset(kind)
    if path is None:
        raise HTTPException(status_code=404, detail="not_found")
    return Response(
        content=path.read_bytes(),
        media_type=branding.content_type_for(path),
        headers={"Cache-Control": "no-cache"},
    )


@router.post(
    "/{kind}",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
async def upload_asset(kind: str, file: UploadFile = File(...)) -> dict[str, object]:
    _validate_kind(kind)
    name = (file.filename or "").lower()
    ext = f".{name.rsplit('.', 1)[-1]}" if "." in name else ""
    allowed = branding.allowed_ext(kind)
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"extensão não permitida; use: {', '.join(sorted(allowed))}",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="arquivo vazio")
    if len(data) > branding.MAX_BYTES:
        raise HTTPException(status_code=400, detail="arquivo muito grande (máx 2 MB)")
    branding.save_asset(kind, ext, data)
    log.info("branding_uploaded", kind=kind, ext=ext, bytes=len(data))
    return {"ok": True, "kind": kind, "bytes": len(data)}


@router.delete(
    "/{kind}",
    dependencies=[Depends(require_csrf), Depends(require_admin)],
)
def delete_asset(kind: str) -> dict[str, object]:
    _validate_kind(kind)
    removed = branding.remove_asset(kind)
    log.info("branding_removed", kind=kind, removed=removed)
    return {"ok": True, "removed": removed}
