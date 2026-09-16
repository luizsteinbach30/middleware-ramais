"""Repository for ``app_config`` rows.

Public API:

* ``load_config(db)`` — returns ``AppConfigOut``.
* ``update_config(db, payload, user_id)`` — applies a partial update.

We model the configuration as flat key/value rows; the schemas reshape this
into a nested ``AppConfigOut`` for the API.

**Nenhum segredo se escreve mais por aqui.** O único que existia era o token do
destino do webhook, e o módulo saiu na v2.14.0; o `uscall_token` legado só é
lido para a máscara, e desde a v2.7.0 quem guarda token de USCall é
`domain/uscall/repository.py`. Segredo com cifra vive em quatro lugares, todos
com dono próprio: USCall, broker MQTT, credencial do NOC e a passphrase do
backup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import AppConfig
from middleware_monitor.domain.config.schemas import (
    AppConfigOut,
    AppConfigUpdate,
)


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

    # Botão único: de quanto em quanto tempo o app coleta do USCall e pinga a
    # frota. Em minutos (padrão 60). A chave se chamou `webhook_interval_minutes`
    # até a v2.14.0 — a migration 0014 renomeia, e a leitura da antiga fica aqui
    # porque um banco restaurado de backup anterior ainda traz o nome velho.
    raw = _get(rows, "coleta_interval_minutes", None)
    if raw is None:
        raw = _get(rows, "webhook_interval_minutes", None)
    if raw is not None:
        out.coleta_interval_minutes = max(1, int(raw))
    else:
        legacy_seconds = [
            int(_get(rows, k, "0") or 0)
            for k in ("extensions_interval_seconds", "devices_interval_seconds", "results_interval_seconds")
        ]
        legacy_max = max(legacy_seconds) if any(legacy_seconds) else 0
        out.coleta_interval_minutes = max(1, legacy_max // 60) if legacy_max else 60

    out.ping_timeout_ms = int(_get(rows, "ping_timeout_ms", "1000"))
    out.ping_concurrency = int(_get(rows, "ping_concurrency", "20"))
    out.device_ping_retention_days = int(_get(rows, "device_ping_retention_days", "30"))

    out.collection_retention_days = int(_get(rows, "collection_retention_days", "90"))
    out.system_log_retention_days = int(_get(rows, "system_log_retention_days", "14"))
    out.mqtt_message_retention_days = int(_get(rows, "mqtt_message_retention_days", "7"))
    out.mqtt_message_max_mb = int(_get(rows, "mqtt_message_max_mb", "0"))
    out.extension_event_retention_days = int(
        _get(rows, "extension_event_retention_days", "7")
    )
    out.extension_call_retention_days = int(
        _get(rows, "extension_call_retention_days", "90")
    )
    out.extension_daily_stats_retention_days = int(
        _get(rows, "extension_daily_stats_retention_days", "365")
    )
    out.phone_timezone_mode = str(_get(rows, "phone_timezone_mode", "herdar"))
    out.phone_timezone = str(_get(rows, "phone_timezone", "") or "")
    out.phone_ntp_server = str(_get(rows, "phone_ntp_server", "a.ntp.br") or "a.ntp.br")

    out.auto_reapply_on_recovery = _get(rows, "auto_reapply_on_recovery", "0") in (
        "1", "true", "True", True,
    )
    out.auto_reapply_debounce_minutes = max(
        1, int(_get(rows, "auto_reapply_debounce_minutes", "60") or 60),
    )

    return out


def update_config(db: DBSession, payload: AppConfigUpdate, *, user_id: int | None) -> AppConfigOut:
    def write(k: str, v: Any) -> None:
        _set(db, k, _stringify(v), is_secret=False, user_id=user_id)

    if payload.client_code is not None:
        write("client_code", payload.client_code)
    # uscall_host/token/verify_ssl: desde a v2.7.0 os servidores USCall são
    # geridos pela tabela uscall_servers (domain/uscall/repository.py) — o
    # KV legado não recebe mais escrita (a migration 0007 já o copiou).

    interval_changed = False
    if payload.coleta_interval_minutes is not None:
        write("coleta_interval_minutes", int(payload.coleta_interval_minutes))
        interval_changed = True

    for k in (
        "ping_timeout_ms",
        "ping_concurrency",
        "device_ping_retention_days",
        "collection_retention_days",
        "system_log_retention_days",
        "mqtt_message_retention_days",
        "mqtt_message_max_mb",
        "extension_event_retention_days",
        "extension_call_retention_days",
        "extension_daily_stats_retention_days",
        "phone_timezone_mode",
        "phone_timezone",
        "phone_ntp_server",
        "auto_reapply_on_recovery",
        "auto_reapply_debounce_minutes",
    ):
        v = getattr(payload, k)
        if v is not None:
            write(k, v)

    db.commit()

    # A hora dos telefones é lida em cache (uma vez por processo, ver
    # `time_settings.global_settings`): sem isto o operador salvaria o fuso e não
    # veria efeito nenhum até reiniciar o serviço.
    try:
        from middleware_monitor.domain.extension_configurator.time_settings import (
            invalidate_cache,
        )

        invalidate_cache()
    except Exception:  # pragma: no cover - invalidação nunca derruba o save
        pass

    # Apply scheduler reschedule if intervals changed.
    if interval_changed:
        try:
            from middleware_monitor.core.scheduler import reschedule

            current = load_config(db)
            seconds = max(60, current.coleta_interval_minutes * 60)
            reschedule("collect_extensions", seconds)
            reschedule("monitor_devices", seconds)
        except Exception:
            pass

    return load_config(db)


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    return str(value)
