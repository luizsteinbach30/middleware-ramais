"""Repository for the ``collections`` table (raw USCall snapshots)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import Collection


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def save_snapshot(db: DBSession, *, type_: str, payload: Any) -> Collection:
    serialized = json.dumps(payload, ensure_ascii=False)
    hash_ = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    item = Collection(
        type=type_,
        collected_at=_now(),
        payload=serialized,
        payload_hash=hash_,
        size_bytes=len(serialized.encode("utf-8")),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def list_snapshots(
    db: DBSession,
    *,
    type_: str | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[Collection], int]:
    stmt = select(Collection)
    if type_ and type_ != "all":
        stmt = stmt.where(Collection.type == type_)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(Collection.collected_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        ).all()
    )
    return rows, total


def get_snapshot(db: DBSession, snapshot_id: int) -> Collection | None:
    return db.get(Collection, snapshot_id)


def latest(db: DBSession, *, type_: str = "extensions") -> Collection | None:
    return db.scalar(
        select(Collection).where(Collection.type == type_).order_by(Collection.collected_at.desc()).limit(1)
    )
