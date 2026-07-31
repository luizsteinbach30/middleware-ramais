"""SQLAlchemy 2.0 models for the entire application.

Naming conventions:
* timestamps in UTC (``datetime.now(UTC)`` at write-site).
* ``payload`` columns are JSON-encoded text (SQLite has no native JSON type but
  understands JSON1 functions when needed).
* every mutable table has ``created_at`` and, when relevant, ``updated_at``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from middleware_monitor.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="admin", nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class UscallServer(Base):
    """Servidor USCall configurado (multi-servidor desde a v2.7.0).

    ``token`` guarda o ciphertext SecretBox (mesma cifra da ``app_config``);
    nunca exponha o valor decriptado fora de jobs/integrações.
    """

    __tablename__ = "uscall_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(64), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    token: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    devices: Mapped[list[Device]] = relationship(back_populates="uscall_server")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    mac: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logical_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    network_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    network_status_prev: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Origem da coleta (multi-USCall). Ramais não se repetem entre servidores,
    # então `name` continua único global; o campo serve para observabilidade e
    # para a verificação de registro SIP consultar o servidor certo.
    uscall_server_id: Mapped[int | None] = mapped_column(
        ForeignKey("uscall_servers.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    pings: Mapped[list[DevicePing]] = relationship(back_populates="device", cascade="all, delete-orphan")
    extension_lines: Mapped[list[ExtensionLine]] = relationship(back_populates="device")
    uscall_server: Mapped[UscallServer | None] = relationship(back_populates="devices")


class DevicePing(Base):
    __tablename__ = "device_pings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    online: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    device: Mapped[Device] = relationship(back_populates="pings")

    __table_args__ = (Index("ix_device_pings_device_ts", "device_id", "timestamp"),)


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_replay: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    replay_of: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SystemLog(Base):
    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)


class UpdateHistory(Base):
    __tablename__ = "update_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    from_version: Mapped[str] = mapped_column(String(32), nullable=False)
    to_version: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # success | failed | rolled_back
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class ExtensionEnvironment(Base):
    """Ambiente do Configurador de Ramais.

    `config_padrao` é JSON serializado (Text) com defaults compartilhados pelas
    linhas do ambiente: sip_server, web_user/password, ntp, timezone, keylock_*,
    validar_conectividade, function_keys, etc.
    """

    __tablename__ = "extension_environments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nome: Mapped[str] = mapped_column(String(128), nullable=False)
    modelo_telefone: Mapped[str] = mapped_column(String(64), nullable=False)
    config_padrao: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    lines: Mapped[list[ExtensionLine]] = relationship(
        back_populates="environment", cascade="all, delete-orphan",
    )
    runs: Mapped[list[ExtensionApplyRun]] = relationship(
        back_populates="environment", cascade="all, delete-orphan",
    )


class ExtensionLine(Base):
    """Linha (telefone) dentro de um Ambiente."""

    __tablename__ = "extension_lines"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid hex
    environment_id: Mapped[str] = mapped_column(
        ForeignKey("extension_environments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ip: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    numero_ramal: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    user_auth: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    senha_sip: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    servidor_sip: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    numero_abreviado: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    nome_visivel: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # Posição da linha na planilha (ordem canônica de exibição/aplicação).
    posicao: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    ultimo_hash_aplicado: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ultimo_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ultima_aplicacao: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ultimo_erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    ultimo_modelo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ultimo_mac: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    environment: Mapped[ExtensionEnvironment] = relationship(back_populates="lines")
    device: Mapped[Device | None] = relationship(back_populates="extension_lines")
    reapply_events: Mapped[list[LineReapplyEvent]] = relationship(
        back_populates="line", cascade="all, delete-orphan",
    )


class ExtensionApplyRun(Base):
    """Histórico de execuções de aplicação em massa (Relatórios)."""

    __tablename__ = "extension_apply_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[str] = mapped_column(
        ForeignKey("extension_environments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ok: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    falha: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    forcado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    operador: Mapped[str | None] = mapped_column(String(64), nullable=True)

    environment: Mapped[ExtensionEnvironment] = relationship(back_populates="runs")
    run_lines: Mapped[list[ExtensionApplyRunLine]] = relationship(
        back_populates="run", cascade="all, delete-orphan",
    )


class ExtensionApplyRunLine(Base):
    """Snapshot por linha impactada numa execução (Relatório).

    Diferente das linhas vivas do ambiente, este é o registro **imutável** do
    que aconteceu naquele run: status antes de aplicar, resultado depois, e os
    dados da linha no momento (sobrevivem a edição/remoção da linha). Só as
    linhas que entraram no run (`pick_lines_to_apply`) viram registro aqui.
    """

    __tablename__ = "extension_apply_run_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("extension_apply_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    line_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    numero_ramal: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    nome_visivel: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # status_antes: pending | applied | outdated | error (derivado no disparo)
    status_antes: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # status_depois: running | ok | erro
    status_depois: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    modelo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # registro SIP pós-aplicação (verificado via USCall): registered|unregistered|skipped
    registro_sip: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    run: Mapped[ExtensionApplyRun] = relationship(back_populates="run_lines")


class LineReapplyEvent(Base):
    """Histórico de reapplies automáticos (watcher de recovery) por linha.

    Separado de `extension_apply_runs` porque cada evento aqui é por linha
    individual, com motivo (`recovery` | `manual_device_page`) e referência
    opcional ao run gerado.
    """

    __tablename__ = "line_reapply_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[str] = mapped_column(
        ForeignKey("extension_lines.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | erro | skipped
    reason: Mapped[str] = mapped_column(String(32), nullable=False)  # recovery | manual_device_page
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("extension_apply_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    line: Mapped[ExtensionLine] = relationship(back_populates="reapply_events")


class DeviceActionEvent(Base):
    """Auditoria de ações remotas nos telefones (v2.7.0).

    Registro flat (não acoplado ao padrão de run de aplicação): cada execução de
    ação (``normalize``, ``set_ip``...) num telefone gera uma linha, com quem
    disparou, o resultado e o erro.
    """

    __tablename__ = "device_action_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    environment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    line_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    vendor: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | erro
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    operador: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__: list[str] = [
    "AppConfig",
    "Collection",
    "Device",
    "DeviceActionEvent",
    "DevicePing",
    "ExtensionApplyRun",
    "ExtensionApplyRunLine",
    "ExtensionEnvironment",
    "ExtensionLine",
    "LineReapplyEvent",
    "LoginAttempt",
    "Session",
    "SystemLog",
    "UpdateHistory",
    "User",
    "WebhookEvent",
]
