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
    # Estado de telefonia detalhado (v2.8.0), vindo do MQTT em tempo real:
    # disponivel | tocando | discando | ocupado | indisponivel | desconhecido.
    # Vive separado de `logical_status` porque este último responde uma pergunta
    # diferente — "o ramal está registrado no PBX?" — e é ele que decide se a
    # configuração do telefone precisa ser reaplicada. Um ramal em conversa está
    # registrado; misturar as duas coisas reaplicaria config no meio da ligação.
    telephony_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    telephony_status_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # A outra ponta da chamada em curso (vazio quando o ramal está livre).
    telephony_numero: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Quem escreveu o último estado lógico: "mqtt" (instantâneo) ou "uscall"
    # (ciclo de coleta REST). A tela mostra a origem para não deixar dúvida
    # sobre a idade do dado.
    status_source: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
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


class MqttBroker(Base):
    """Broker MQTT/EMQX assinado pelo coletor de mensagens.

    Segue o padrão de ``uscall_servers``: a senha guarda o ciphertext da
    ``SecretBox`` (mesma cifra da ``app_config``) e nunca sai em texto claro
    para a API/UI. ``address_input`` preserva o que o operador digitou — o
    host/porta/transporte ao lado são o resultado da descoberta automática.
    """

    __tablename__ = "mqtt_brokers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(64), nullable=False)
    address_input: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    transport: Mapped[str] = mapped_column(String(16), nullable=False, default="tcp")  # tcp | websockets
    tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ws_path: Mapped[str | None] = mapped_column(String(128), nullable=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    password: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tls_verify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Impressão digital SHA-256 do certificado aceito pelo operador. Quando
    # preenchida, substitui a validação por CA: aceita aquele certificado e
    # nenhum outro (melhor que o "tls_insecure" genérico).
    tls_fingerprint: Mapped[str | None] = mapped_column(String(95), nullable=True)
    topics: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON: filtros MQTT
    qos: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    clean_session: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    max_payload_kb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MqttMessage(Base):
    """Mensagem recebida do broker, gravada como veio — é o comprovante.

    ``payload`` é o corpo verbatim (base64 quando binário, ``b64=True``). Os
    campos derivados (``ramal``, ``event_at``) existem só para a busca; a prova
    é o par ``received_at`` + ``topic`` + ``payload``.
    """

    __tablename__ = "mqtt_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker_id: Mapped[int | None] = mapped_column(
        ForeignKey("mqtt_brokers.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    topic: Mapped[str] = mapped_column(String(512), nullable=False)
    ramal: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retained: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    b64: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Evidência fixada pelo operador: imune à retenção.
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_mqtt_messages_topic_ts", "topic", "received_at"),
        Index("ix_mqtt_messages_ramal_ts", "ramal", "received_at"),
        Index("ix_mqtt_messages_pinned", "pinned"),
    )


class MqttConnectionEvent(Base):
    """Histórico de conexão do coletor — a prova de cobertura.

    Sem ele, a ausência de uma mensagem no período não prova nada: pode ter
    sido o coletor que estava fora do ar.
    """

    __tablename__ = "mqtt_connection_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    # startup | connected | subscribed | disconnected | error | stopped
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class ExtensionStatusEvent(Base):
    """Transição de estado de um ramal, normalizada a partir do MQTT.

    Só **transições** entram aqui: repetição do mesmo estado não acrescenta
    informação. A mensagem crua continua inteira no ledger — ``message_id``
    aponta para ela, que é o comprovante. Quanto o filtro corta depende do
    publicador: o do cliente já fala só na mudança (~99% viram linha), mas a
    reentrega da sessão durável e um publicador que varra periodicamente
    repetiriam estado — é para esses que o filtro existe.

    ``call_started_at`` é o campo ``duracao`` do payload (horário de início da
    chamada em curso, não uma duração); é o que permite reconstruir a chamada
    quando o ``uniqueid`` não vem.
    """

    __tablename__ = "extension_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ramal: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # canônico
    status_raw: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    numero: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uniqueid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    call_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("mqtt_messages.id", ondelete="SET NULL"), nullable=True,
    )

    __table_args__ = (
        Index("ix_extension_status_events_ramal_ts", "ramal", "received_at"),
    )


class ExtensionCall(Base):
    """Chamada reconstruída a partir das transições de estado do ramal.

    **Uma linha por perna da chamada**, não por conversa: numa ligação interna o
    PBX publica os dois ramais, e cada um vira uma linha. ``uniqueid`` amarra as
    pernas quando o publicador o envia — e ele NÃO é único por par: um grupo de
    captura toca vários ramais com o mesmo id (medido: até 5 pernas).

    Como o trecho é delimitado (medido em produção, 2026-08-21): uma perna é uma
    sequência ininterrupta de ``tocando``/``discando``/``ocupado`` do mesmo
    ramal, fechada por ``disponivel``/``indisponivel``. **Não** se usa o campo
    ``duracao`` do payload como chave: ele varia em 98% das chamadas (1161 de
    1183), porque marca o início do *estado atual* e não o da chamada — ele
    serve para medir toque e conversa, não para identificar.
    """

    __tablename__ = "extension_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ramal: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # entrante | sainte | desconhecida
    direcao: Mapped[str] = mapped_column(String(16), nullable=False, default="desconhecida")
    numero: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uniqueid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ring_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    talk_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # atendida | perdida | nao_atendida | indeterminada | em_curso
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="em_curso")
    # Último evento consumido: torna a reconstrução retomável e idempotente.
    last_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_extension_calls_ramal_started", "ramal", "started_at"),
        Index("ix_extension_calls_outcome", "outcome"),
    )


class ExtensionDailyStat(Base):
    """Resumo por ramal e por dia — sobrevive à poda das transições.

    As transições têm retenção curta (7 dias por padrão); este resumo fica por um
    ano, então é dele que sai qualquer comparação histórica.
    """

    __tablename__ = "extension_daily_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dia: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # AAAA-MM-DD
    ramal: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chamadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    atendidas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    perdidas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entrantes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saintes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    talk_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ring_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_extension_daily_stats_dia_ramal", "dia", "ramal", unique=True),)


__all__: list[str] = [
    "AppConfig",
    "Collection",
    "Device",
    "DeviceActionEvent",
    "DevicePing",
    "ExtensionApplyRun",
    "ExtensionApplyRunLine",
    "ExtensionCall",
    "ExtensionDailyStat",
    "ExtensionEnvironment",
    "ExtensionLine",
    "ExtensionStatusEvent",
    "LineReapplyEvent",
    "LoginAttempt",
    "MqttBroker",
    "MqttConnectionEvent",
    "MqttMessage",
    "Session",
    "SystemLog",
    "UpdateHistory",
    "User",
    "WebhookEvent",
]
