"""Repository for ``app_config`` rows.

Public API:

* ``load_config(db)`` — returns ``AppConfigOut`` (secrets masked as ``"set"``).
* ``load_secret(db, key)`` — returns the decrypted plaintext for internal use
  by jobs/integrations. Never expose this to the API/UI.
* ``update_config(db, payload, user_id)`` — applies a partial update,
  encrypting secret fields with the application ``SecretBox``.

We model the configuration as flat key/value rows; the schemas reshape this
into a nested ``AppConfigOut`` for the API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.crypto import SecretBox
from middleware_monitor.core.models import AppConfig
from middleware_monitor.domain.config.schemas import (
    AppConfigOut,
    AppConfigUpdate,
    WebhookConfig,
)
from middleware_monitor.settings import get_settings

WEBHOOK_TYPES: tuple[str, ...] = ("extensions", "devices", "results")

_SECRET_KEYS = {"uscall_token"} | {f"webhooks.{t}.token" for t in WEBHOOK_TYPES}


def _box() -> SecretBox:
    return SecretBox(get_settings().secret_key)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _all_rows(db: DBSession) -> dict[str, AppConfig]:
    return {row.key: row for row in db.scalars(select(AppConfig)).all()}


def _set(db: DBSession, key: str, value: str, *, is_secret: bool, user_id: int | None) -> None:
    rows = _all_rows(db)
    row = rows.get(key)
    if row is None:
        row = AppConfig(key=key, value=value, is_secret=is_secret, updated_at=_now(), updated_by=user_id)
        db.add(row)
    else:
        row.value = value
        row.is_secret = is_secret
        row.updated_at = _now()
        row.updated_by = user_id


def _get(rows: dict[str, AppConfig], key: str, default: Any = None) -> Any:
    row = rows.get(key)
    if row is None:
        return default
    return row.value


def _has_secret(rows: dict[str, AppConfig], key: str) -> bool:
    row = rows.get(key)
    return bool(row and row.is_secret and row.value)


def load_config(db: DBSession) -> AppConfigOut:
    rows = _all_rows(db)
    out = AppConfigOut()
    out.client_code = str(_get(rows, "client_code", ""))
    out.uscall_host = str(_get(rows, "uscall_host", ""))
    out.uscall_token = "set" if _has_secret(rows, "uscall_token") else None
    out.uscall_verify_ssl = _get(rows, "uscall_verify_ssl", "1") in ("1", "true", "True", True)

    # Single knob that controls how often the app collects and dispatches
    # webhooks. Stored in minutes (default 60); legacy installs may still have
    # the old per-type "..._interval_seconds" rows in the DB — fall back to
    # the largest of them so we never silently speed things up after upgrade.
    raw = _get(rows, "webhook_interval_minutes", None)
    if raw is not None:
        out.webhook_interval_minutes = max(1, int(raw))
    else:
        legacy_seconds = [
            int(_get(rows, k, "0") or 0)
            for k in ("extensions_interval_seconds", "devices_interval_seconds", "results_interval_seconds")
        ]
        legacy_max = max(legacy_seconds) if any(legacy_seconds) else 0
        out.webhook_interval_minutes = max(1, legacy_max // 60) if legacy_max else 60

    out.ping_timeout_ms = int(_get(rows, "ping_timeout_ms", "1000"))
    out.ping_concurrency = int(_get(rows, "ping_concurrency", "20"))
    out.device_ping_retention_days = int(_get(rows, "device_ping_retention_days", "30"))

    out.webhook_log_retention_days = int(_get(rows, "webhook_log_retention_days", "30"))
    out.collection_retention_days = int(_get(rows, "collection_retention_days", "90"))
    out.system_log_retention_days = int(_get(rows, "system_log_retention_days", "14"))
    out.mqtt_message_retention_days = int(_get(rows, "mqtt_message_retention_days", "7"))
    out.mqtt_message_max_mb = int(_get(rows, "mqtt_message_max_mb", "0"))
    out.webhook_timeout_seconds = int(_get(rows, "webhook_timeout_seconds", "10"))

    out.auto_reapply_on_recovery = _get(rows, "auto_reapply_on_recovery", "0") in (
        "1", "true", "True", True,
    )
    out.auto_reapply_debounce_minutes = max(
        1, int(_get(rows, "auto_reapply_debounce_minutes", "60") or 60),
    )

    webhooks: dict[str, WebhookConfig] = {}
    for t in WEBHOOK_TYPES:
        webhooks[t] = WebhookConfig(
            enabled=_get(rows, f"webhooks.{t}.enabled", "0") in ("1", "true", "True", True),
            url=str(_get(rows, f"webhooks.{t}.url", "")),
            token="set" if _has_secret(rows, f"webhooks.{t}.token") else None,
            last_status=_get(rows, f"webhooks.{t}.last_status", None),
        )
    out.webhooks = webhooks
    return out


def load_secret(db: DBSession, key: str) -> str | None:
    if key not in _SECRET_KEYS:
        raise ValueError(f"{key!r} is not a known secret key")
    rows = _all_rows(db)
    row = rows.get(key)
    if row is None or not row.value:
        return None
    return _box().decrypt(row.value)


def update_config(db: DBSession, payload: AppConfigUpdate, *, user_id: int | None) -> AppConfigOut:
    box = _box()

    def write(k: str, v: Any, *, secret: bool = False) -> None:
        _set(db, k, v if secret else _stringify(v), is_secret=secret, user_id=user_id)

    if payload.client_code is not None:
        write("client_code", payload.client_code)
    # uscall_host/token/verify_ssl: desde a v2.7.0 os servidores USCall são
    # geridos pela tabela uscall_servers (domain/uscall/repository.py) — o
    # KV legado não recebe mais escrita (a migration 0007 já o copiou).

    interval_changed = False
    if payload.webhook_interval_minutes is not None:
        write("webhook_interval_minutes", int(payload.webhook_interval_minutes))
        interval_changed = True

    for k in (
        "ping_timeout_ms",
        "ping_concurrency",
        "device_ping_retention_days",
        "webhook_log_retention_days",
        "collection_retention_days",
        "system_log_retention_days",
        "mqtt_message_retention_days",
        "mqtt_message_max_mb",
        "webhook_timeout_seconds",
        "auto_reapply_on_recovery",
        "auto_reapply_debounce_minutes",
    ):
        v = getattr(payload, k)
        if v is not None:
            write(k, v)

    if payload.webhooks:
        for t, cfg in payload.webhooks.items():
            if t not in WEBHOOK_TYPES:
                continue
            if cfg.enabled is not None:
                write(f"webhooks.{t}.enabled", cfg.enabled)
            if cfg.url is not None:
                write(f"webhooks.{t}.url", cfg.url.strip())
            if cfg.token is not None:
                if cfg.token == "":
                    write(f"webhooks.{t}.token", "", secret=True)
                else:
                    write(f"webhooks.{t}.token", box.encrypt(cfg.token), secret=True)

    db.commit()

    # Apply scheduler reschedule if intervals changed.
    if interval_changed:
        try:
            from middleware_monitor.core.scheduler import reschedule

            current = load_config(db)
            seconds = max(60, current.webhook_interval_minutes * 60)
            reschedule("collect_extensions", seconds)
            reschedule("monitor_devices", seconds)
        except Exception:
            pass

    return load_config(db)


def update_webhook_status(db: DBSession, event_type: str, status: str) -> None:
    _set(db, f"webhooks.{event_type}.last_status", status, is_secret=False, user_id=None)
    db.commit()


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    return str(value)
