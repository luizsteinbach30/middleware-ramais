"""Sonda de descoberta: transforma o que o operador digitou em um endpoint real.

O que a tela promete é que ninguém precisa saber o que é porta, transporte ou
TLS. Aqui isso é levado a sério: nada é deduzido do texto digitado — cada
candidato é **testado na rede** e só é aceito quando o broker responde CONNACK
de MQTT. Porta aberta não é prova de nada; painel web na 18083 é o engano mais
comum de quem copia o endereço do navegador, e a sonda o identifica e sugere a
porta certa.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt

from middleware_monitor.core.logging import get_logger
from middleware_monitor.domain.mqtt.address import (
    Candidate,
    ParsedAddress,
    build_candidates,
    parse_broker_address,
)
from middleware_monitor.domain.mqtt.parser import parse_extension_payload
from middleware_monitor.integrations.mqtt_client import (
    MqttAuth,
    MqttEndpoint,
    connack_message,
    fingerprint_sha256,
    is_auth_failure,
    paho_transport,
    reason_code_value,
)

__all__ = [
    "DEFAULT_SNIFF_FILTERS",
    "CertInfo",
    "DiscoveryReport",
    "ProbeResult",
    "TopicSniffResult",
    "discover",
    "sniff_topics",
]

log = get_logger("mqtt.discovery")

TCP_TIMEOUT = 2.5
TLS_TIMEOUT = 3.0
MQTT_TIMEOUT = 4.0
MAX_CANDIDATES = 8


@dataclass(slots=True)
class CertInfo:
    subject: str = ""
    issuer: str = ""
    not_after: str = ""
    fingerprint: str = ""
    trusted_by_ca: bool = False
    error: str = ""


@dataclass(slots=True)
class ProbeResult:
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
    cert: CertInfo | None = None


@dataclass(slots=True)
class DiscoveryReport:
    address_input: str
    host: str = ""
    resolved: list[str] = field(default_factory=list)
    dns_error: str = ""
    results: list[ProbeResult] = field(default_factory=list)
    chosen: ProbeResult | None = None
    needs_credentials: bool = False
    needs_cert_trust: bool = False
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def success(self) -> bool:
        return self.chosen is not None


def discover(address: str, *, username: str = "", password: str = "") -> DiscoveryReport:
    """Descobre porta, transporte e TLS a partir do endereço digitado.

    Bloqueante (usa sockets) — chame de fora do loop, com ``asyncio.to_thread``.
    """
    report = DiscoveryReport(address_input=address)
    try:
        parsed = parse_broker_address(address)
    except ValueError as exc:
        report.error = str(exc)
        return report

    report.host = parsed.host
    try:
        infos = socket.getaddrinfo(parsed.host, None, proto=socket.IPPROTO_TCP)
        report.resolved = sorted({str(i[4][0]) for i in infos})
    except OSError as exc:
        report.dns_error = str(exc)
        report.error = "dns_falhou"
        return report

    if parsed.dashboard_port:
        report.notes.append(
            f"A porta {parsed.port} é a do painel web do EMQX, não a do MQTT — "
            "vou testar também as portas padrão do broker."
        )

    candidates = build_candidates(parsed)[:MAX_CANDIDATES]
    auth = MqttAuth(username=username, password=password)
    with ThreadPoolExecutor(max_workers=min(6, len(candidates) or 1)) as pool:
        report.results = list(pool.map(lambda c: _probe(c, auth), candidates))

    ok = [r for r in report.results if r.mqtt_ok]
    if ok:
        report.chosen = _escolher(ok, parsed)
        report.needs_cert_trust = bool(
            report.chosen.cert is not None and not report.chosen.cert.trusted_by_ca
        )
        if report.needs_cert_trust:
            report.notes.append(
                "O broker apresenta um certificado que não é assinado por uma autoridade "
                "conhecida. Confira a impressão digital e confie nele para gravar."
            )
        return report

    auth_fail = [r for r in report.results if r.auth_required]
    if auth_fail:
        report.chosen = None
        report.needs_credentials = True
        report.error = "credenciais_recusadas" if username else "credenciais_necessarias"
        report.notes.append(
            "O broker respondeu, mas recusou o acesso: "
            + (auth_fail[0].detail or "informe usuário e senha.")
        )
        return report

    http = [r for r in report.results if r.http_server]
    if http:
        report.notes.append(
            f"A porta {http[0].port} responde HTTP ({http[0].server_hint or 'servidor web'}), "
            "não MQTT. Use o endereço do broker, não o do painel."
        )
    report.error = "nenhum_endpoint_mqtt"
    return report


def _escolher(ok: list[ProbeResult], parsed: ParsedAddress) -> ProbeResult:
    """Qual dos endpoints que responderam vira a configuração gravada.

    Se o operador foi explícito (esquema **e** porta) e aquilo funciona, a
    vontade dele manda — inclusive para escolher texto puro numa rede onde o
    TLS é bloqueado. Fora isso, ganha o mais seguro e direto: TLS antes de
    texto puro, MQTT nativo antes de websocket, e o mais rápido como desempate.
    Sem essa regra, colar a URL do painel (http://host:18083) acabaria gravando
    um websocket sem criptografia só porque o texto dizia "http".
    """
    if parsed.scheme is not None and parsed.port is not None and not parsed.dashboard_port:
        for r in ok:
            if (r.port, r.transport, r.tls) == (parsed.port, parsed.transport, parsed.tls):
                return r
    return min(
        ok,
        key=lambda r: (not r.tls, r.transport == "websockets", r.latency_ms or 9999),
    )


# ── sondagem de um candidato ─────────────────────────────────────────────────


def _probe(cand: Candidate, auth: MqttAuth) -> ProbeResult:
    res = ProbeResult(
        label=cand.label, host=cand.host, port=cand.port, transport=cand.transport,
        tls=cand.tls, ws_path=cand.ws_path, reason=cand.reason,
    )
    started = time.perf_counter()
    try:
        with socket.create_connection((cand.host, cand.port), timeout=TCP_TIMEOUT):
            res.tcp_ok = True
            res.latency_ms = int((time.perf_counter() - started) * 1000)
    except OSError as exc:
        res.detail = _os_error_pt(exc)
        return res

    if cand.tls:
        res.cert = _inspect_cert(cand.host, cand.port)
        res.tls_ok = not res.cert.error
        if not res.tls_ok:
            res.detail = f"falha no TLS: {res.cert.error}"
            # Pode ser um servidor em texto puro nessa porta — vale checar HTTP.
            _detect_http(cand, res, use_tls=False)
            return res

    _mqtt_probe(cand, auth, res)
    if not res.mqtt_ok and not res.auth_required:
        _detect_http(cand, res, use_tls=cand.tls)
    return res


def _inspect_cert(host: str, port: int) -> CertInfo:
    info = CertInfo()
    # 1ª tentativa: validação normal, para saber se uma CA conhecida assina.
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=TLS_TIMEOUT) as sock, ctx.wrap_socket(
            sock, server_hostname=host
        ) as tls_sock:
            info.trusted_by_ca = True
            _fill_cert(info, tls_sock)
            return info
    except ssl.SSLError as exc:
        info.error = _ssl_error_pt(exc)
    except OSError as exc:
        info.error = _os_error_pt(exc)
        return info

    # 2ª tentativa: sem validar, só para exibir o certificado ao operador.
    raw = ssl.create_default_context()
    raw.check_hostname = False
    raw.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=TLS_TIMEOUT) as sock, raw.wrap_socket(
            sock, server_hostname=host
        ) as tls_sock:
            info.error = ""
            info.trusted_by_ca = False
            _fill_cert(info, tls_sock)
    except (ssl.SSLError, OSError) as exc:
        info.error = _ssl_error_pt(exc) if isinstance(exc, ssl.SSLError) else _os_error_pt(exc)
    return info


def _fill_cert(info: CertInfo, tls_sock: ssl.SSLSocket) -> None:
    der = tls_sock.getpeercert(binary_form=True)
    if der:
        info.fingerprint = fingerprint_sha256(der)
    cert = tls_sock.getpeercert()
    if isinstance(cert, dict) and cert:
        info.subject = _rdn(cert.get("subject"))
        info.issuer = _rdn(cert.get("issuer"))
        info.not_after = str(cert.get("notAfter") or "")
    elif der:
        # Sem validação o dicionário vem vazio — e é justamente no certificado
        # auto-assinado que o operador precisa ver quem o emitiu e até quando
        # vale para decidir se confia. Lê direto do DER.
        _fill_cert_from_der(info, der)


def _fill_cert_from_der(info: CertInfo, der: bytes) -> None:
    try:
        from cryptography import x509

        cert = x509.load_der_x509_certificate(der)
        info.subject = cert.subject.rfc4514_string()
        info.issuer = cert.issuer.rfc4514_string()
        info.not_after = cert.not_valid_after_utc.strftime("%Y-%m-%d %H:%M UTC")
    except Exception as exc:  # pragma: no cover - certificado exótico
        log.warning("mqtt_cert_parse_failed", error=type(exc).__name__, message=str(exc))


def _rdn(value: object) -> str:
    if not isinstance(value, tuple):
        return ""
    partes: list[str] = []
    for rdn in value:
        if not isinstance(rdn, tuple):
            continue
        for item in rdn:
            if isinstance(item, tuple) and len(item) == 2:
                partes.append(f"{item[0]}={item[1]}")
    return ", ".join(partes)


def _mqtt_probe(cand: Candidate, auth: MqttAuth, res: ProbeResult) -> None:
    """Conecta de verdade e espera o CONNACK — a única prova de que é MQTT.

    Usa ``clean_session=True`` e um ``client_id`` descartável para não encostar
    na sessão durável do coletor.
    """
    done = threading.Event()
    outcome: dict[str, int] = {}

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=_probe_client_id(),
        transport=paho_transport(cand.transport),
        clean_session=True,
        protocol=mqtt.MQTTv311,
    )
    if cand.transport == "websockets":
        client.ws_set_options(path=cand.ws_path or "/mqtt")
    if auth.username:
        client.username_pw_set(auth.username, auth.password or None)
    if cand.tls:
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)

    def on_connect(_c: mqtt.Client, _u: object, _f: object, rc: object, _p: object = None) -> None:
        outcome["rc"] = reason_code_value(rc)
        done.set()

    client.on_connect = on_connect
    try:
        client.connect_async(cand.host, cand.port, keepalive=15)
        client.loop_start()
        if not done.wait(MQTT_TIMEOUT):
            res.detail = "sem resposta MQTT (a porta abre, mas o broker não respondeu)"
            return
        code = outcome.get("rc", 0)
        res.connack = code
        res.detail = connack_message(code)
        if code == 0:
            res.mqtt_ok = True
            res.tls_ok = res.tls_ok or cand.tls
        elif is_auth_failure(code):
            res.auth_required = True
    except (OSError, ValueError) as exc:
        res.detail = _os_error_pt(exc) if isinstance(exc, OSError) else str(exc)
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # pragma: no cover - encerramento best effort
            pass


def _detect_http(cand: Candidate, res: ProbeResult, *, use_tls: bool) -> None:
    """Descobre se quem atende ali é um servidor web (o caso do painel EMQX)."""
    request = (
        f"GET / HTTP/1.1\r\nHost: {cand.host}\r\nConnection: close\r\n"
        "User-Agent: MiddlewareMonitor-probe\r\n\r\n"
    ).encode()
    try:
        with socket.create_connection((cand.host, cand.port), timeout=TCP_TIMEOUT) as sock:
            stream: socket.socket | ssl.SSLSocket = sock
            if use_tls:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                stream = ctx.wrap_socket(sock, server_hostname=cand.host)
            stream.settimeout(TCP_TIMEOUT)
            stream.sendall(request)
            head = stream.recv(2048)
    except (OSError, ssl.SSLError):
        return
    texto = head.decode("latin-1", errors="replace")
    if not texto.startswith("HTTP/"):
        return
    res.http_server = True
    baixo = texto.lower()
    if "emqx" in baixo:
        res.server_hint = "painel web do EMQX"
    elif "dashboard" in baixo:
        res.server_hint = "painel web"
    else:
        res.server_hint = "servidor web"
    res.detail = f"responde HTTP ({res.server_hint}), não MQTT"


def _probe_client_id() -> str:
    import secrets

    return f"mwmonitor-probe-{secrets.token_hex(4)}"


def _os_error_pt(exc: OSError) -> str:
    nome = type(exc).__name__
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return "tempo esgotado (sem resposta)"
    if isinstance(exc, ConnectionRefusedError):
        return "conexão recusada (porta fechada)"
    if isinstance(exc, socket.gaierror):
        return "não foi possível resolver o nome"
    return f"{nome}: {exc}"


def _ssl_error_pt(exc: ssl.SSLError) -> str:
    texto = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in texto:
        if "self-signed" in texto or "self signed" in texto:
            return "certificado auto-assinado"
        if "expired" in texto.lower():
            return "certificado expirado"
        return "certificado não confiável"
    if "WRONG_VERSION_NUMBER" in texto or "record layer failure" in texto:
        return "a porta não fala TLS"
    return texto


# ── amostragem de tópicos ────────────────────────────────────────────────────


@dataclass(slots=True)
class TopicSniffResult:
    seconds: int
    messages: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    samples: dict[str, str] = field(default_factory=dict)
    recognized: set[str] = field(default_factory=set)
    filter_used: str = ""
    denied: list[str] = field(default_factory=list)
    error: str = ""


# Escada de filtros: brokers costumam ter ACL negando "#" (é o caso do EMQX do
# cliente). Em vez de devolver "0 mensagens" e deixar o operador no escuro,
# tenta ramos progressivamente mais específicos e diz na tela qual valeu.
DEFAULT_SNIFF_FILTERS: tuple[str, ...] = ("#", "v1/#", "v1/data/#")


def sniff_topics(
    endpoint: MqttEndpoint,
    auth: MqttAuth,
    *,
    seconds: int = 8,
    filters: Sequence[str] = DEFAULT_SNIFF_FILTERS,
    tls_verify: bool = True,
    tls_fingerprint: str | None = None,
    max_topics: int = 2000,
) -> TopicSniffResult:
    """Escuta o broker por alguns segundos e devolve o que existe lá.

    É o que permite ao operador **escolher** o tópico em vez de digitar um
    palpite. Assina com sessão descartável para não interferir na do coletor.
    """
    result = TopicSniffResult(seconds=seconds)
    conectado = threading.Event()
    assinado = threading.Event()
    falhou: dict[str, str] = {}
    pendente: dict[str, str] = {}

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=_probe_client_id(),
        transport=paho_transport(endpoint.transport),
        clean_session=True,
        protocol=mqtt.MQTTv311,
    )
    if endpoint.transport == "websockets":
        client.ws_set_options(path=endpoint.ws_path or "/mqtt")
    if auth.username:
        client.username_pw_set(auth.username, auth.password or None)
    if endpoint.tls:
        if tls_verify and not tls_fingerprint:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        else:
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)

    def on_connect(_c: mqtt.Client, _u: object, _f: object, rc: object, _p: object = None) -> None:
        code = reason_code_value(rc)
        if code != 0:
            falhou["erro"] = connack_message(code)
        conectado.set()

    def on_subscribe(
        _c: mqtt.Client, _u: object, _mid: int,
        rcs: Sequence[object] | None = None, _p: object = None,
    ) -> None:
        # ACL do broker aparece aqui, não no CONNACK: assinar "#" pode ser
        # negado mesmo com a conexão aceita.
        recusado = any(getattr(rc, "is_failure", False) for rc in (rcs or ()))
        filtro = pendente.get("atual", "")
        if recusado:
            result.denied.append(filtro)
        else:
            result.filter_used = filtro
        assinado.set()

    def on_message(_c: mqtt.Client, _u: object, msg: mqtt.MQTTMessage) -> None:
        result.messages += 1
        topico = msg.topic
        if topico in result.counts or len(result.counts) < max_topics:
            result.counts[topico] = result.counts.get(topico, 0) + 1
        corpo = (msg.payload or b"").decode("utf-8", errors="replace")
        if topico not in result.samples:
            result.samples[topico] = corpo[:2000]
        if topico not in result.recognized and parse_extension_payload(corpo):
            result.recognized.add(topico)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    try:
        client.connect_async(endpoint.host, endpoint.port, keepalive=15)
        client.loop_start()
        if not conectado.wait(MQTT_TIMEOUT):
            result.error = "sem resposta do broker"
            return result
        if falhou:
            result.error = falhou["erro"]
            return result

        for filtro in filters:
            pendente["atual"] = filtro
            assinado.clear()
            client.subscribe(filtro, qos=0)
            if not assinado.wait(MQTT_TIMEOUT):
                result.error = f"o broker não respondeu à assinatura de {filtro}"
                return result
            if result.filter_used:
                break
        if not result.filter_used:
            result.error = (
                "o broker não permite escutar "
                + ", ".join(result.denied)
                + " — informe o ramo que você quer registrar (ex.: v1/data/#)"
            )
            return result

        time.sleep(max(1, min(seconds, 30)))
    except (OSError, ValueError) as exc:
        result.error = _os_error_pt(exc) if isinstance(exc, OSError) else str(exc)
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:  # pragma: no cover
            pass
    return result
