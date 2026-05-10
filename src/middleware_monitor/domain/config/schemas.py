"""Pydantic schemas for the application config (read by the UI / API).

Secret fields are NEVER returned with their plaintext value. They are exposed
either as ``None`` (not set) or the literal string ``"set"`` (set, but masked).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class WebhookConfig(BaseModel):
    enabled: bool = False
    url: str = ""
    token: Literal["set"] | None = None
    last_status: str | None = None


class WebhookConfigUpdate(BaseModel):
    enabled: bool | None = None
    url: str | None = None
    # Only present when the operator wants to change it. ``""`` means clear.
    token: str | None = None


class AppConfigOut(BaseModel):
    client_code: str = ""
    uscall_host: str = ""
    uscall_token: Literal["set"] | None = None
    uscall_verify_ssl: bool = True

    extensions_interval_seconds: int = 60
    devices_interval_seconds: int = 30
    results_interval_seconds: int = 300

    ping_timeout_ms: int = 1000
    ping_concurrency: int = 20
    device_ping_retention_days: int = 30

    webhook_log_retention_days: int = 30
    collection_retention_days: int = 90
    system_log_retention_days: int = 14

    webhook_timeout_seconds: int = 10

    webhooks: dict[str, WebhookConfig] = Field(
        default_factory=lambda: {
            "extensions": WebhookConfig(),
            "devices": WebhookConfig(),
            "results": WebhookConfig(),
        }
    )


class AppConfigUpdate(BaseModel):
    client_code: str | None = None
    uscall_host: str | None = None
    uscall_token: str | None = None
    uscall_verify_ssl: bool | None = None

    extensions_interval_seconds: int | None = Field(default=None, ge=10)
    devices_interval_seconds: int | None = Field(default=None, ge=10)
    results_interval_seconds: int | None = Field(default=None, ge=10)

    ping_timeout_ms: int | None = Field(default=None, ge=100, le=10_000)
    ping_concurrency: int | None = Field(default=None, ge=1, le=200)
    device_ping_retention_days: int | None = Field(default=None, ge=1, le=365)

    webhook_log_retention_days: int | None = Field(default=None, ge=1, le=365)
    collection_retention_days: int | None = Field(default=None, ge=1, le=365)
    system_log_retention_days: int | None = Field(default=None, ge=1, le=365)
    webhook_timeout_seconds: int | None = Field(default=None, ge=1, le=120)

    webhooks: dict[str, WebhookConfigUpdate] | None = None


class UscallTestRequest(BaseModel):
    host: str | None = None
    token: str | None = None


class UscallTestResponse(BaseModel):
    success: bool
    http_status: int | None = None
    latency_ms: int | None = None
    error: str | None = None


__all__: list[str] = [
    "AppConfigOut",
    "AppConfigUpdate",
    "UscallTestRequest",
    "UscallTestResponse",
    "WebhookConfig",
    "WebhookConfigUpdate",
]


def _ensure_url(value: str) -> HttpUrl:
    return HttpUrl(value)
