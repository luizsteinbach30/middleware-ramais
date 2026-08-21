"""Cliente MQTT (paho) com reconexão automática e pinning de certificado.

A biblioteca roda em thread própria (``loop_start``); os callbacks só empurram
dados para quem chamou — nada de I/O de banco aqui, para a ingestão nunca
segurar a thread de rede.

Escolha deliberada: ``clean_session=False`` com ``client_id`` estável e QoS 1.
É o que faz o broker guardar as mensagens enquanto o serviço está parado — sem
isso, um reinício vira um buraco permanente no registro, e o registro é
justamente a razão de existir desta integração.
"""

from __future__ import annotations

import hashlib
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import paho.mqtt.client as mqtt

from middleware_monitor.core.logging import get_logger

__all__ = [
    "AUTH_FAILURE_CODES",
    "MqttAuth",
    "MqttConnection",
    "MqttEndpoint",
    "connack_message",
    "fingerprint_sha256",
    "is_auth_failure",
    "paho_transport",
    "reason_code_value",
]

log = get_logger("mqtt.client")

# Quanto esperar o DISCONNECT sair do fio antes de matar a thread de rede. Curto
# de proposito: e caminho de parada e de troca de configuracao, e travar a tela
# do operador por causa de um broker que nao responde seria pior que a sessao
# ficar pendurada.
DISCONNECT_TIMEOUT = 1.5

# Conectamos em MQTT 3.1.1, onde o broker NAO tem como dizer por que derrubou:
# ele so fecha o socket e o paho reporta "conexao perdida". Entao a deteccao de
# client_id duplicado nao pode ser por codigo — tem de ser pelo padrao. Duas
# instancias com o mesmo identificador se derrubam em revezamento, o que produz
# um ciclo de reconexao rapido e sem fim.
FLAP_JANELA_S = 120.0
FLAP_LIMITE = 5

# Mensagens de CONNACK em português — o operador vê a causa, não o número.
#
# O paho 2.x traduz o retorno do MQTT 3.1.1 para os códigos do MQTT 5 (senha
# errada chega como 134, não 4), então as duas numerações precisam estar aqui —
# senão "usuário ou senha inválidos" viraria "recusado pelo broker (134)" e a
# tela nunca pediria credenciais.
_CONNACK_PT: dict[int, str] = {
    0: "conexão aceita",
    1: "broker recusou a versão do protocolo MQTT",
    2: "identificador de cliente recusado pelo broker",
    3: "broker indisponível",
    4: "usuário ou senha inválidos",
    5: "não autorizado no broker (usuário sem permissão)",
    128: "recusado pelo broker (erro não especificado)",
    132: "broker recusou a versão do protocolo MQTT",
    133: "identificador de cliente recusado pelo broker",
    134: "usuário ou senha inválidos",
    135: "não autorizado no broker (usuário sem permissão)",
    136: "broker indisponível",
    137: "broker ocupado",
}

# Códigos que significam "o broker respondeu, mas recusou o acesso".
AUTH_FAILURE_CODES: frozenset[int] = frozenset({4, 5, 134, 135})


def connack_message(code: int) -> str:
    return _CONNACK_PT.get(code, f"recusado pelo broker (código {code})")


def is_auth_failure(code: int) -> bool:
    return code in AUTH_FAILURE_CODES


def paho_transport(transport: str) -> Literal["tcp", "websockets"]:
    """Normaliza o transporte para o literal que o paho aceita."""
    return "websockets" if transport == "websockets" else "tcp"


def reason_code_value(rc: object) -> int:
    """Código numérico do CONNACK/desconexão (o paho 2.x devolve objeto)."""
    valor = getattr(rc, "value", rc)
    if isinstance(valor, bool):
        return int(valor)
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str) and valor.isdigit():
        return int(valor)
    return 0


def fingerprint_sha256(der: bytes) -> str:
    """``AA:BB:...`` — o formato que aparece no navegador e nos manuais."""
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


@dataclass(slots=True)
class MqttEndpoint:
    host: str
    port: int
    transport: str = "tcp"  # tcp | websockets
    tls: bool = False
    ws_path: str | None = None

    @property
    def label(self) -> str:
        if self.transport == "websockets":
            proto = "wss" if self.tls else "ws"
            return f"{proto}://{self.host}:{self.port}{self.ws_path or '/mqtt'}"
        proto = "ssl" if self.tls else "tcp"
        return f"{proto}://{self.host}:{self.port}"


@dataclass(slots=True)
class MqttAuth:
    username: str = ""
    password: str = ""


@dataclass(slots=True)
class MqttConnection:
    """Uma assinatura viva no broker.

    ``on_message`` recebe ``(topic, payload, qos, retained)`` na thread do paho.
    ``on_state`` recebe ``(state, detail)`` com ``state`` em
    ``connected | disconnected | subscribed | error``.
    """

    endpoint: MqttEndpoint
    client_id: str
    topics: list[str]
    auth: MqttAuth = field(default_factory=MqttAuth)
    qos: int = 1
    clean_session: bool = False
    tls_verify: bool = True
    tls_fingerprint: str | None = None
    keepalive: int = 60
    on_message: Callable[[str, bytes, int, bool], None] | None = None
    on_state: Callable[[str, str], None] | None = None

    _client: mqtt.Client | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _state: str = field(default="disconnected", init=False)
    _last_error: str = field(default="", init=False)
    # Sinalizado pelo `on_disconnect`. Existe para o `stop()` conseguir esperar
    # o DISCONNECT sair do fio antes de matar a thread de rede.
    _disconnected: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False,
    )
    _quedas: deque[float] = field(default_factory=deque, init=False, repr=False)

    # ── ciclo de vida ────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._client is not None:
                return
            client = self._build_client()
            self._client = client
        self._disconnected.clear()
        self._set_state("connecting", f"conectando em {self.endpoint.label}")
        client.connect_async(self.endpoint.host, self.endpoint.port, keepalive=self.keepalive)
        client.loop_start()

    def stop(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        if client is None:
            return
        try:
            self._disconnected.clear()
            client.disconnect()
            # `disconnect()` só ENFILEIRA o pacote — quem o escreve no socket é a
            # thread de rede. Chamar `loop_stop()` na sequência costumava matar
            # essa thread antes do DISCONNECT sair, e o broker via a conexão cair
            # de forma suja. Com `clean_session=False` isso é caro: o EMQX
            # mantém a sessão viva esperando o cliente voltar, guardando fila —
            # é a "sessão fechada" que fica pendurada no broker.
            if not self._disconnected.wait(timeout=DISCONNECT_TIMEOUT):
                log.warning(
                    "mqtt_disconnect_sem_confirmacao",
                    client_id=self.client_id, endpoint=self.endpoint.label,
                )
            client.loop_stop()
        except Exception as exc:  # pragma: no cover - encerramento best effort
            log.warning("mqtt_stop_failed", error=type(exc).__name__, message=str(exc))
        self._set_state("disconnected", "coletor parado")

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_error(self) -> str:
        return self._last_error

    # ── construção ───────────────────────────────────────────────────────────

    def _build_client(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            transport=paho_transport(self.endpoint.transport),
            clean_session=self.clean_session,
            protocol=mqtt.MQTTv311,
        )
        if self.endpoint.transport == "websockets":
            client.ws_set_options(path=self.endpoint.ws_path or "/mqtt")
        if self.auth.username:
            client.username_pw_set(self.auth.username, self.auth.password or None)
        if self.endpoint.tls:
            self._apply_tls(client)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message
        return client

    def _apply_tls(self, client: mqtt.Client) -> None:
        if self.tls_fingerprint:
            # Certificado fixado: aceita aquele certificado e nenhum outro. A
            # conferência acontece no on_connect, com o socket já em mãos.
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)
            return
        if self.tls_verify:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
            return
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)

    # ── callbacks (thread do paho) ───────────────────────────────────────────

    def _on_connect(
        self, client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _props: Any = None,
    ) -> None:
        code = reason_code_value(reason_code)
        if code != 0:
            self._set_state("error", connack_message(code))
            return
        if self.tls_fingerprint and not self._fingerprint_ok(client):
            return
        self._set_state("connected", f"conectado em {self.endpoint.label}")
        subs = [(t, self.qos) for t in self.topics]
        if subs:
            client.subscribe(subs)

    def _fingerprint_ok(self, client: mqtt.Client) -> bool:
        try:
            sock = cast("ssl.SSLSocket | None", client.socket())
            der = sock.getpeercert(binary_form=True) if sock is not None else None
        except Exception as exc:  # pragma: no cover - depende do socket real
            der = None
            log.warning("mqtt_peercert_failed", error=type(exc).__name__, message=str(exc))
        if not der:
            self._set_state("error", "não foi possível ler o certificado do broker")
            client.disconnect()
            return False
        atual = fingerprint_sha256(der)
        if atual != self.tls_fingerprint:
            self._set_state(
                "error",
                "o certificado do broker mudou — impressão digital "
                f"{atual} difere da confiada {self.tls_fingerprint}",
            )
            client.disconnect()
            return False
        return True

    def _on_disconnect(
        self, _client: mqtt.Client, _userdata: Any, _flags: Any = None,
        reason_code: Any = None, _props: Any = None,
    ) -> None:
        code = reason_code_value(reason_code)
        self._disconnected.set()
        if code == 0:
            self._set_state("disconnected", "desconectado do broker")
            return
        # Queda anormal. O broker reconecta sozinho (reconnect_delay_set), mas o
        # motivo importa: "session taken over" quer dizer que OUTRA instância
        # entrou com o mesmo client_id, e as duas passam a se derrubar em
        # revezamento — o sintoma é reconexão sem fim e sessão nova a cada
        # rodada no EMQX. Sem dizer isso em voz alta, o operador só vê o coletor
        # "piscando" e não tem como saber a causa.
        detail = f"conexão perdida (código {code})"
        if self._registrar_queda_e_detectar_flap():
            detail = (
                f"reconexão em ciclo — o identificador {self.client_id} pode estar "
                f"em uso por outra instância apontando para este mesmo broker"
            )
            log.error(
                "mqtt_reconexao_em_ciclo",
                client_id=self.client_id,
                endpoint=self.endpoint.label,
                quedas=len(self._quedas),
                janela_s=FLAP_JANELA_S,
            )
        self._set_state("disconnected", detail)

    def _registrar_queda_e_detectar_flap(self) -> bool:
        """Anota a queda e diz se o coletor está reconectando em ciclo.

        Duas instâncias com o mesmo ``client_id`` se derrubam em revezamento, e
        cada rodada deixa uma sessão nova pendurada no broker. Do lado de cá o
        sintoma é indistinguível de rede ruim — só a **frequência** separa os
        dois casos, e é isso que se mede aqui.
        """
        agora = time.monotonic()
        self._quedas.append(agora)
        while self._quedas and agora - self._quedas[0] > FLAP_JANELA_S:
            self._quedas.popleft()
        return len(self._quedas) >= FLAP_LIMITE

    def _on_subscribe(
        self, _client: mqtt.Client, _userdata: Any, _mid: int,
        reason_codes: Any = None, _props: Any = None,
    ) -> None:
        recusados = [
            str(rc) for rc in (reason_codes or []) if getattr(rc, "is_failure", False)
        ]
        if recusados:
            self._set_state("error", f"broker recusou a assinatura: {', '.join(recusados)}")
            return
        self._set_state("subscribed", "assinado: " + ", ".join(self.topics))

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        if self.on_message is None:
            return
        try:
            self.on_message(message.topic, message.payload or b"", int(message.qos), bool(message.retain))
        except Exception as exc:  # pragma: no cover - callback do chamador
            log.error("mqtt_on_message_failed", error=type(exc).__name__, message=str(exc))

    # ── estado ───────────────────────────────────────────────────────────────

    def _set_state(self, state: str, detail: str) -> None:
        self._state = state
        if state == "error":
            self._last_error = detail
        elif state in ("connected", "subscribed"):
            self._last_error = ""
        if self.on_state is not None:
            try:
                self.on_state(state, detail)
            except Exception as exc:  # pragma: no cover - callback do chamador
                log.error("mqtt_on_state_failed", error=type(exc).__name__, message=str(exc))
