"""Pydantic schemas do Configurador de Ramais."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer

from middleware_monitor.core.time import iso_utc


class FunctionKeySpec(BaseModel):
    key: str = Field(min_length=1, max_length=32)
    type: Literal["disabled", "speed_dial", "line", "blf"] = "speed_dial"
    label: str = ""
    value_source: Literal["linha", "fixo"] = "linha"
    value_field: str = "numero_abreviado"
    value_fixed: str = ""
    account: int = Field(default=1, ge=0, le=255)


class ConfigPadrao(BaseModel):
    """Snapshot do `config_padrao` desserializado.

    Mantemos campos opcionais para tolerar versões antigas do JSON salvo —
    o repository merge com defaults antes de devolver, então o caller sempre
    vê o conjunto completo.
    """

    sip_server: str = ""
    sip_transport: str = "udp"
    web_user: str = "admin"
    web_password: str = "admin"
    nova_web_user: str = ""
    nova_web_password: str = ""
    ntp_server: str = "a.ntp.br"
    timezone: str = "America/Sao_Paulo"
    web_language: str = "pt-BR"
    lcd_language: str = "pt-BR"
    register_expiration: int = 30
    validar_conectividade: bool = True
    menu_password: str = "123"
    keylock_password: str = "123"
    keylock_enable: int = 2
    keylock_timeout: int = 30
    function_keys: list[FunctionKeySpec] = Field(default_factory=list)


class EnvironmentIn(BaseModel):
    nome: str = Field(min_length=1, max_length=128)
    modelo_telefone: str = Field(min_length=1, max_length=64)


class EnvironmentUpdate(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=128)
    config_padrao: dict[str, Any] | None = None


class EnvironmentOut(BaseModel):
    id: str
    nome: str
    modelo_telefone: str
    config_padrao: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", when_used="json-unless-none")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class LineIn(BaseModel):
    ip: str = ""
    numero_ramal: str = ""
    user_auth: str = ""
    senha_sip: str = ""
    servidor_sip: str = ""
    numero_abreviado: str = ""
    nome_visivel: str = ""


class LineOut(LineIn):
    id: str
    environment_id: str
    ultimo_hash_aplicado: str | None = None
    ultimo_status: str | None = None
    ultima_aplicacao: datetime | None = None
    ultimo_erro: str | None = None
    ultimo_modelo: str | None = None
    ultimo_mac: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "ultima_aplicacao", "created_at", "updated_at",
        when_used="json-unless-none",
    )
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class ApplyRunOut(BaseModel):
    id: int
    environment_id: str
    started_at: datetime
    finished_at: datetime | None
    total: int
    ok: int
    falha: int
    forcado: bool
    operador: str | None

    @field_serializer("started_at", "finished_at", when_used="json-unless-none")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)
