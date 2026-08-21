"""Pydantic schemas for the application config (read by the UI / API).

Secret fields are NEVER returned with their plaintext value. They are exposed
either as ``None`` (not set) or the literal string ``"set"`` (set, but masked).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class UscallServerOut(BaseModel):
    """Servidor USCall como exposto na API/UI (token sempre mascarado)."""

    id: int
    nome: str
    host: str
    token: Literal["set"] | None = None
    verify_ssl: bool = True
    enabled: bool = True


class UscallServerIn(BaseModel):
    """Criação/edição de servidor USCall.

    ``token``: ``None`` ou o literal ``"set"`` mantém o token atual (edição);
    string não-vazia define/re-cifra; ``""`` é inválido (token obrigatório).
    """

    nome: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    token: str | None = None
    verify_ssl: bool = True
    enabled: bool = True


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
    # Campos legados do modo servidor único (leitura KV). Continuam expostos
    # até os consumidores migrarem para `uscall_servers` (PR do coletor);
    # a UI/PUT não escrevem mais neles.
    uscall_host: str = ""
    uscall_token: Literal["set"] | None = None
    uscall_verify_ssl: bool = True

    # Multi-servidor (v2.7.0): fonte da verdade é a tabela uscall_servers.
    uscall_servers: list[UscallServerOut] = Field(default_factory=list)

    webhook_interval_minutes: int = 60

    ping_timeout_ms: int = 1000
    ping_concurrency: int = 20
    device_ping_retention_days: int = 30

    webhook_log_retention_days: int = 30
    collection_retention_days: int = 90
    system_log_retention_days: int = 14

    # Ledger MQTT (v2.8.0). O limite por espaco vale sobre o volume de payload
    # gravado; 0 = sem limite. Mensagens fixadas como evidencia sao imunes.
    mqtt_message_retention_days: int = 7
    mqtt_message_max_mb: int = 0
    # Transicoes normalizadas dos ramais: alimentam o painel ao vivo e a linha
    # do tempo. Linha pequena (sem corpo da mensagem), mas o volume acompanha o
    # movimento do PBX — nao presuma que e desprezivel diante do ledger.
    extension_event_retention_days: int = 7

    webhook_timeout_seconds: int = 10

    auto_reapply_on_recovery: bool = False
    auto_reapply_debounce_minutes: int = 60

    webhooks: dict[str, WebhookConfig] = Field(
        default_factory=lambda: {
            "extensions": WebhookConfig(),
            "devices": WebhookConfig(),
            "results": WebhookConfig(),
        }
    )


class AppConfigUpdate(BaseModel):
    client_code: str | None = None
    # uscall_host/token/verify_ssl sairam daqui na v2.7.0 — servidores USCall
    # agora são geridos pelo CRUD /api/config/uscall-servers.

    webhook_interval_minutes: int | None = Field(default=None, ge=1, le=1440)

    ping_timeout_ms: int | None = Field(default=None, ge=100, le=10_000)
    ping_concurrency: int | None = Field(default=None, ge=1, le=200)
    device_ping_retention_days: int | None = Field(default=None, ge=1, le=365)

    webhook_log_retention_days: int | None = Field(default=None, ge=1, le=365)
    collection_retention_days: int | None = Field(default=None, ge=1, le=365)
    system_log_retention_days: int | None = Field(default=None, ge=1, le=365)
    mqtt_message_retention_days: int | None = Field(default=None, ge=1, le=365)
    mqtt_message_max_mb: int | None = Field(default=None, ge=0, le=1_000_000)
    extension_event_retention_days: int | None = Field(default=None, ge=1, le=365)
    webhook_timeout_seconds: int | None = Field(default=None, ge=1, le=120)

    auto_reapply_on_recovery: bool | None = None
    auto_reapply_debounce_minutes: int | None = Field(default=None, ge=1, le=10080)

    webhooks: dict[str, WebhookConfigUpdate] | None = None


class UscallTestRequest(BaseModel):
    # server_id: testa um servidor salvo (credenciais do banco). Alternativa:
    # host+token (form ainda não salvo, ex.: modal de novo servidor).
    server_id: int | None = None
    host: str | None = None
    token: str | None = None
    verify_ssl: bool | None = None


class UscallTestResponse(BaseModel):
    success: bool
    http_status: int | None = None
    latency_ms: int | None = None
    error: str | None = None


__all__: list[str] = [
    "AppConfigOut",
    "AppConfigUpdate",
    "UscallServerIn",
    "UscallServerOut",
    "UscallTestRequest",
    "UscallTestResponse",
    "WebhookConfig",
    "WebhookConfigUpdate",
]


def _ensure_url(value: str) -> HttpUrl:
    return HttpUrl(value)
