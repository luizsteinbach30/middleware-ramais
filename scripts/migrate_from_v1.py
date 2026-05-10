"""Migrate v1.0 JSON state (data/*.json) into the v2.0 SQLite database.

Idempotent: rows already present (by hash, by name) are not duplicated.
Run once after installing v2.0 on a server that previously hosted v1.0.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from middleware_monitor.core.db import init_engine, session_factory
from middleware_monitor.core.models import Collection, Device, WebhookEvent
from middleware_monitor.domain.config.repository import update_config
from middleware_monitor.domain.config.schemas import (
    AppConfigUpdate,
    WebhookConfigUpdate,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _read(path: Path) -> object:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8") or "null")
    except json.JSONDecodeError:
        return None


def main(legacy_dir: str = "data") -> int:
    base = Path(legacy_dir)
    if not base.exists():
        print(f"No legacy directory found at {base}; nothing to do.")
        return 0
    init_engine()

    config = _read(base / "config.json") or {}
    devices = _read(base / "devices.json") or []
    webhook_logs = _read(base / "webhook_logs.json") or []

    with session_factory() as db:
        # Config
        webhooks_payload: dict[str, WebhookConfigUpdate] = {}
        for k in ("extensions", "devices", "results"):
            wh = (config.get("webhooks") or {}).get(k) or {}
            webhooks_payload[k] = WebhookConfigUpdate(
                enabled=bool(wh.get("enabled", False)),
                url=wh.get("url") or "",
                token=wh.get("token") if wh.get("token") else None,
            )
        update_config(
            db,
            AppConfigUpdate(
                client_code=config.get("client_code") or "",
                uscall_host=config.get("uscall_host") or "",
                uscall_token=config.get("uscall_token") or None,
                extensions_interval_seconds=config.get("extensions_interval") or None,
                devices_interval_seconds=config.get("devices_interval") or None,
                results_interval_seconds=config.get("results_interval") or None,
                webhooks=webhooks_payload,
            ),
            user_id=None,
        )

        # Devices
        for d in devices or []:
            name = str(d.get("name") or "").strip()
            if not name:
                continue
            existing = db.scalar(select(Device).where(Device.name == name))
            if existing:
                continue
            db.add(
                Device(
                    name=name,
                    ip=d.get("ip") or None,
                    mac=d.get("mac") or None,
                    logical_status="available" if d.get("logical_status") == "disponivel" else "unknown",
                    network_status=d.get("status") or "unknown",
                    latency_ms=d.get("latency"),
                    created_at=_now(),
                    updated_at=_now(),
                )
            )

        # Webhook events
        for w in webhook_logs or []:
            payload = json.dumps(w.get("payload") or {}, ensure_ascii=False)
            db.add(
                WebhookEvent(
                    timestamp=_parse_ts(w.get("timestamp")) or _now(),
                    event_type=w.get("type") or "unknown",
                    url=w.get("url") or "",
                    http_status=w.get("http_status"),
                    duration_ms=int(w.get("duration_ms") or 0),
                    success=bool(w.get("success")),
                    error=w.get("error"),
                    payload=payload,
                    response_body=w.get("response_body"),
                )
            )

        # Existing snapshots
        coll = base / "collections" / "extensions"
        if coll.exists():
            for f in sorted(coll.iterdir()):
                if not f.suffix == ".json":
                    continue
                content = f.read_text(encoding="utf-8")
                h = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if db.scalar(select(Collection).where(Collection.payload_hash == h)):
                    continue
                db.add(
                    Collection(
                        type="extensions",
                        collected_at=_parse_ts(f.stem.replace("_", " ")) or _now(),
                        payload=content,
                        payload_hash=h,
                        size_bytes=len(content.encode("utf-8")),
                    )
                )

        db.commit()
    print("Migration completed.")
    return 0


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H-%M-%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
