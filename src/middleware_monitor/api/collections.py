"""Collections endpoints (raw USCall snapshots)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import get_current_user, get_session
from middleware_monitor.core.models import User
from middleware_monitor.domain.collections.repository import (
    get_snapshot,
    list_snapshots,
)

router = APIRouter(prefix="/api/collections", tags=["collections"])


class SnapshotMeta(BaseModel):
    id: int
    type: str
    collected_at: str
    payload_hash: str
    size_bytes: int


class SnapshotPage(BaseModel):
    items: list[SnapshotMeta]
    total: int
    page: int
    size: int


@router.get("", response_model=SnapshotPage)
def list_(
    type_: str | None = Query(default=None, alias="type"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> SnapshotPage:
    rows, total = list_snapshots(db, type_=type_, page=page, size=size)
    return SnapshotPage(
        items=[
            SnapshotMeta(
                id=r.id,
                type=r.type,
                collected_at=r.collected_at.isoformat(timespec="seconds"),
                payload_hash=r.payload_hash[:12],
                size_bytes=r.size_bytes,
            )
            for r in rows
        ],
        total=total,
        page=page,
        size=size,
    )


@router.get("/{snapshot_id}")
def detail(
    snapshot_id: int,
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, object]:
    snap = get_snapshot(db, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="not_found")
    try:
        payload = json.loads(snap.payload)
    except json.JSONDecodeError:
        payload = snap.payload
    return {
        "id": snap.id,
        "type": snap.type,
        "collected_at": snap.collected_at.isoformat(timespec="seconds"),
        "payload_hash": snap.payload_hash,
        "size_bytes": snap.size_bytes,
        "payload": payload,
    }
