"""Schemas da API de MQTT.

A senha do broker nunca sai em texto claro: na leitura ela vira ``"set"`` ou
``None``, como o token dos servidores USCall.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer

from middleware_monitor.core.time import iso_utc

__all__ = [
    "BrokerIn",
    "BrokerOut",
    "CallOut",
    "CallsPage",
    "CoverageGap",
    "CoverageOut",
    "DailyStatOut",
    "DiscoverRequest",
    "DiscoverResponse",
    "LiveExtension",
    "LiveOut",
    "LiveTransition",
    "MessageDetail",
    "MessageOut",
    "MessagesPage",
    "PinRequest",
    "ProbeOut",
    "SniffBranch",
    "SniffRequest",
    "SniffResponse",
    "StatusOut",
]


class BrokerOut(BaseModel):
    id: int
    nome: str
    address_input: str = ""
    host: str
    port: int
    transport: str = "tcp"
    tls: bool = False
    ws_path: str | None = None
    username: str = ""
    password: Literal["set"] | None = None
    tls_verify: bool = True
    tls_fingerprint: str | None = None
    topics: list[str] = Field(default_factory=list)
    qos: int = 1
    clean_session: bool = False
    client_id: str = ""
    max_payload_kb: int = 0
    enabled: bool = True

    @property
    def endpoint(self) -> str:
        if self.transport == "websockets":
            return f"{'wss' if self.tls else 'ws'}://{self.host}:{self.port}{self.ws_path or '/mqtt'}"
        return f"{'ssl' if self.tls else 'tcp'}://{self.host}:{self.port}"


class BrokerIn(BaseModel):
    """Criação/edição do broker.

    O formulário manda o resultado da descoberta (host/porta/transporte/TLS);
    ``password``: ``None``/``"set"`` mantém, string re-cifra, ``""`` limpa.
    """

    nome: str = Field(min_length=1, max_length=64)
    address_input: str = Field(default="", max_length=255)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    transport: Literal["tcp", "websockets"] = "tcp"
    tls: bool = False
    ws_path: str | None = Field(default=None, max_length=128)
    username: str = Field(default="", max_length=128)
    password: str | None = None
    tls_verify: bool = True
    tls_fingerprint: str | None = Field(default=None, max_length=95)
    topics: list[str] = Field(default_factory=list)
    qos: int = Field(default=1, ge=0, le=2)
    max_payload_kb: int = Field(default=0, ge=0, le=10_240)
    enabled: bool = True


class DiscoverRequest(BaseModel):
    address: str = Field(min_length=1, max_length=255)
    username: str = ""
    password: str | None = None
    # Reaproveita a senha já gravada quando o operador não redigita.
    broker_id: int | None = None


class ProbeOut(BaseModel):
    label: str
    host: str
    port: int
    transport: str
    tls: bool
    ws_path: str | None = None
    reason: str = ""
    tcp_ok: bool = False
    latency_ms: int | None = None
    tls_ok: bool = False
    mqtt_ok: bool = False
    connack: int | None = None
    detail: str = ""
    auth_required: bool = False
    http_server: bool = False
    server_hint: str = ""
    cert_subject: str = ""
    cert_issuer: str = ""
    cert_not_after: str = ""
    cert_fingerprint: str = ""
    cert_trusted_by_ca: bool = False
    cert_error: str = ""


class DiscoverResponse(BaseModel):
    success: bool
    address_input: str
    host: str = ""
    resolved: list[str] = Field(default_factory=list)
    dns_error: str = ""
    results: list[ProbeOut] = Field(default_factory=list)
    chosen: ProbeOut | None = None
    needs_credentials: bool = False
    needs_cert_trust: bool = False
    notes: list[str] = Field(default_factory=list)
    error: str = ""


class SniffRequest(BaseModel):
    """Amostragem de tópicos: ou pelos dados do formulário, ou por broker salvo."""

    broker_id: int | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    transport: Literal["tcp", "websockets"] = "tcp"
    tls: bool = False
    ws_path: str | None = None
    username: str = ""
    password: str | None = None
    tls_verify: bool = True
    tls_fingerprint: str | None = None
    seconds: int = Field(default=8, ge=2, le=30)
    # Vazio = escada padrao ("#" e, se o broker recusar por ACL, ramos mais
    # especificos). Preenchido = escuta exatamente o que o operador pediu.
    filter: str = ""


class SniffBranch(BaseModel):
    filter: str
    messages: int
    topics: int
    samples: list[str] = Field(default_factory=list)
    recognized: bool = False
    sample_payload: str = ""


class SniffResponse(BaseModel):
    success: bool
    seconds: int
    messages: int = 0
    topics: int = 0
    branches: list[SniffBranch] = Field(default_factory=list)
    filter_used: str = ""
    denied: list[str] = Field(default_factory=list)
    error: str = ""


class BrokerStatus(BaseModel):
    broker_id: int
    nome: str
    endpoint: str
    client_id: str
    topics: list[str] = Field(default_factory=list)
    state: str
    detail: str = ""
    connected_since: datetime | None = None
    received: int = 0

    @field_serializer("connected_since")
    def _ser_since(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class StatusOut(BaseModel):
    running: bool = False
    configured: bool = False
    started_at: datetime | None = None
    brokers: list[BrokerStatus] = Field(default_factory=list)
    received: int = 0
    persisted: int = 0
    dropped: int = 0
    persist_failures: int = 0
    queue_depth: int = 0
    per_minute: int = 0
    last_message_at: datetime | None = None
    avg_lag_seconds: float | None = None
    # Mensagens com a hora do PBX a mais de 1h do relógio do servidor.
    clock_outliers: int = 0
    # Fase 3: quanto do fluxo virou informação, não só linha no ledger.
    transitions: int = 0
    devices_touched: int = 0
    tracked_ramais: int = 0
    stored_messages: int = 0
    stored_payload_bytes: int = 0
    retention_days: int = 7
    retention_max_mb: int = 0

    @field_serializer("started_at", "last_message_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class MessageOut(BaseModel):
    id: int
    received_at: datetime
    topic: str
    ramal: str | None = None
    qos: int = 0
    retained: bool = False
    b64: bool = False
    truncated: bool = False
    pinned: bool = False
    payload_bytes: int = 0
    event_at: datetime | None = None
    preview: str = ""

    @field_serializer("received_at", "event_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class MessageDetail(MessageOut):
    payload: str = ""
    pretty: str | None = None
    broker: str = ""


class MessagesPage(BaseModel):
    items: list[MessageOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 100
    truncated: bool = False
    # False quando a contagem parou no teto de varredura (filtro com curinga no
    # meio, que não vira SQL): a tela mostra "mais de N" em vez de um número
    # exato que seria mentira.
    exact_total: bool = True


class PinRequest(BaseModel):
    pinned: bool = True


class CoverageGap(BaseModel):
    started_at: datetime
    ended_at: datetime | None = None
    seconds: int = 0
    detail: str = ""

    @field_serializer("started_at", "ended_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class CoverageOut(BaseModel):
    """Quanto do período consultado o coletor esteve realmente ouvindo.

    Sem isso, "não há mensagem no período" não prova nada — pode ter sido o
    coletor que estava fora do ar.
    """

    since: datetime
    until: datetime
    covered_seconds: int = 0
    total_seconds: int = 0
    coverage_pct: float = 0.0
    gaps: list[CoverageGap] = Field(default_factory=list)
    unknown: bool = False

    @field_serializer("since", "until")
    def _ser_dt(self, value: datetime) -> str | None:
        return iso_utc(value)


class LiveExtension(BaseModel):
    """Um ramal como ele está agora, na visão do coletor.

    ``device_id`` só vem preenchido quando a coleta REST já criou o telefone —
    o payload MQTT não traz IP nem MAC, então um ramal pode aparecer aqui antes
    de existir como device. Mostrar mesmo assim é proposital: é sinal de ramal
    novo no PBX que a coleta ainda não pegou.
    """

    ramal: str
    status: str
    numero: str | None = None
    uniqueid: str | None = None
    since: datetime | None = None
    seconds_in_status: int = 0
    last_seen_at: datetime | None = None
    seconds_since_message: int = 0
    device_id: int | None = None
    ip: str | None = None
    network_status: str = "unknown"

    @field_serializer("since", "last_seen_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class LiveTransition(BaseModel):
    id: int
    ramal: str
    status: str
    numero: str | None = None
    received_at: datetime
    event_at: datetime | None = None
    message_id: int | None = None

    @field_serializer("received_at", "event_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class LiveOut(BaseModel):
    generated_at: datetime
    running: bool = False
    configured: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    extensions: list[LiveExtension] = Field(default_factory=list)
    transitions: list[LiveTransition] = Field(default_factory=list)
    # Saúde da ingestão, repetida aqui para a tela não precisar de dois fetches.
    per_minute: int = 0
    queue_depth: int = 0
    dropped: int = 0
    avg_lag_seconds: float | None = None
    last_message_at: datetime | None = None

    @field_serializer("generated_at", "last_message_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class CallOut(BaseModel):
    """Uma perna de chamada.

    Numa ligação interna há **duas** linhas (uma por ramal), amarradas pelo
    ``uniqueid``. Num grupo de captura há uma por ramal tocado.
    """

    id: int
    ramal: str
    direcao: str  # entrante | sainte | desconhecida
    numero: str | None = None
    uniqueid: str | None = None
    started_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    ring_seconds: int | None = None
    talk_seconds: int | None = None
    outcome: str

    @field_serializer("started_at", "answered_at", "ended_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class CallsPage(BaseModel):
    items: list[CallOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 100


class DailyStatOut(BaseModel):
    dia: str
    ramal: str
    chamadas: int = 0
    atendidas: int = 0
    perdidas: int = 0
    entrantes: int = 0
    saintes: int = 0
    talk_seconds: int = 0
    ring_seconds: int = 0
