"""System logs endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import get_current_user, get_session
from middleware_monitor.core.models import SystemLog, User

router = APIRouter(prefix="/api/logs", tags=["logs"])


class LogOut(BaseModel):
    id: int
    timestamp: str
    level: str
    module: str
    message: str
    context: str | None


class LogsPage(BaseModel):
    items: list[LogOut]
    total: int
    page: int
    size: int


@router.get("", response_model=LogsPage)
def list_(
    level: str | None = None,
    module: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
    _user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> LogsPage:
    stmt = select(SystemLog)
    if level and level != "all":
        stmt = stmt.where(SystemLog.level == level)
    if module and module != "all":
        stmt = stmt.where(SystemLog.module == module)
    if search:
        stmt = stmt.where(SystemLog.message.ilike(f"%{search}%"))
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(SystemLog.timestamp.desc())
            .offset((page - 1) * size)
            .limit(size)
        ).all()
    )
    return LogsPage(
        items=[
            LogOut(
                id=r.id,
                timestamp=r.timestamp.isoformat(timespec="seconds"),
                level=r.level,
                module=r.module,
                message=r.message,
                context=r.context,
            )
            for r in rows
        ],
        total=total,
        page=page,
        size=size,
    )
