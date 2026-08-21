"""Sonda de descoberta contra servidores locais de mentira.

O contrato que estes testes protegem: **porta aberta não é endpoint MQTT**. Só
vale o que responde CONNACK — e é isso que evita gravar a porta do painel web
como se fosse o broker.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterator

import pytest

from middleware_monitor.domain.mqtt import discovery
from middleware_monitor.domain.mqtt.address import ParsedAddress, parse_broker_address
from middleware_monitor.domain.mqtt.discovery import (
    CertInfo,
    ProbeResult,
    _escolher,
    _probe,
    discover,
)
from middleware_monitor.integrations.mqtt_client import MqttAuth

CONNACK_OK = bytes([0x20, 0x02, 0x00, 0x00])
CONNACK_AUTH = bytes([0x20, 0x02, 0x00, 0x04])
HTTP_EMQX = (
    b"HTTP/1.1 200 OK\r\nServer: EMQX\r\nContent-Type: text/html\r\n\r\n"
    b"<html><title>EMQX Dashboard</title></html>"
)


@pytest.fixture(autouse=True)
def _sonda_rapida(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sem isto cada porta muda custaria 4 s de espera.
    monkeypatch.setattr(discovery, "TCP_TIMEOUT", 0.6)
    monkeypatch.setattr(discovery, "TLS_TIMEOUT", 0.6)
    monkeypatch.setattr(discovery, "MQTT_TIMEOUT", 1.2)


def _servidor(responder: Callable[[socket.socket], None]) -> Iterator[int]:
    """Sobe um servidor TCP efêmero em 127.0.0.1 e devolve a porta."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    porta = srv.getsockname()[1]
    parar = threading.Event()

    def laco() -> None:
        srv.settimeout(0.3)
        while not parar.is_set():
            try:
                conn, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=_atender, args=(conn,), daemon=True).start()

    def _atender(conn: socket.socket) -> None:
        try:
            conn.settimeout(2.0)
            responder(conn)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=laco, daemon=True)
    t.start()
    try:
        yield porta
    finally:
        parar.set()
        t.join(timeout=2)
        srv.close()


@pytest.fixture
def broker_falso() -> Iterator[int]:
    def responder(conn: socket.socket) -> None:
        conn.recv(4096)  # CONNECT
        conn.sendall(CONNACK_OK)
        conn.recv(4096)  # segura a conexão até o cliente desistir

    yield from _servidor(responder)


@pytest.fixture
def broker_sem_credencial() -> Iterator[int]:
    def responder(conn: socket.socket) -> None:
        conn.recv(4096)
        conn.sendall(CONNACK_AUTH)
        conn.recv(4096)

    yield from _servidor(responder)


@pytest.fixture
def painel_web() -> Iterator[int]:
    def responder(conn: socket.socket) -> None:
        conn.recv(4096)
        conn.sendall(HTTP_EMQX)

    yield from _servidor(responder)


@pytest.fixture
def porta_muda() -> Iterator[int]:
    def responder(conn: socket.socket) -> None:
        conn.recv(4096)  # aceita e não responde nada

    yield from _servidor(responder)


def _sonda(porta: int, *, tls: bool = False) -> ProbeResult:
    parsed = ParsedAddress(host="127.0.0.1", port=porta)
    cand = next(
        c
        for c in discovery.build_candidates(parsed)
        if c.port == porta and c.transport == "tcp" and c.tls is tls
    )
    return _probe(cand, MqttAuth())


def test_broker_que_responde_connack_e_aceito(broker_falso: int) -> None:
    r = _sonda(broker_falso)
    assert r.tcp_ok and r.mqtt_ok
    assert r.connack == 0
    assert r.latency_ms is not None


def test_porta_aberta_que_nao_fala_mqtt_nao_e_aceita(porta_muda: int) -> None:
    r = _sonda(porta_muda)
    assert r.tcp_ok is True
    assert r.mqtt_ok is False
    assert "sem resposta MQTT" in r.detail


def test_painel_web_e_identificado_como_http(painel_web: int) -> None:
    r = _sonda(painel_web)
    assert r.mqtt_ok is False
    assert r.http_server is True
    assert r.server_hint == "painel web do EMQX"


def test_credencial_recusada_vira_pedido_de_usuario_e_senha(broker_sem_credencial: int) -> None:
    r = _sonda(broker_sem_credencial)
    assert r.mqtt_ok is False
    assert r.auth_required is True
    assert "usuário ou senha" in r.detail


def test_porta_fechada_reporta_recusa() -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()  # ninguém escutando nessa porta
    r = _sonda(porta)
    assert r.tcp_ok is False
    assert r.mqtt_ok is False
    assert r.detail


def test_descoberta_completa_escolhe_o_endpoint_que_respondeu(broker_falso: int) -> None:
    rel = discover(f"127.0.0.1:{broker_falso}")
    assert rel.success is True
    assert rel.chosen is not None
    assert rel.chosen.port == broker_falso
    assert rel.chosen.transport == "tcp"
    assert rel.error == ""


def test_descoberta_sem_nenhum_endpoint_mqtt(painel_web: int) -> None:
    rel = discover(f"127.0.0.1:{painel_web}")
    assert rel.success is False
    assert rel.error == "nenhum_endpoint_mqtt"
    assert any("não MQTT" in n for n in rel.notes)


def test_descoberta_pede_credenciais_quando_o_broker_recusa(broker_sem_credencial: int) -> None:
    rel = discover(f"127.0.0.1:{broker_sem_credencial}")
    assert rel.success is False
    assert rel.needs_credentials is True
    assert rel.error == "credenciais_necessarias"


def test_dns_que_nao_resolve_para_antes_de_sondar() -> None:
    rel = discover("host-que-nao-existe.invalido")
    assert rel.error == "dns_falhou"
    assert rel.results == []


def test_endereco_invalido_nao_levanta() -> None:
    assert discover("").error == "endereco_vazio"
    assert discover("ftp://x").error.startswith("esquema_desconhecido")


# ── escolha entre endpoints que funcionaram ──────────────────────────────────


def _ok(port: int, transport: str, tls: bool, latency: int = 10) -> ProbeResult:
    return ProbeResult(
        label=f"{port}", host="h", port=port, transport=transport, tls=tls,
        tcp_ok=True, mqtt_ok=True, latency_ms=latency,
        cert=CertInfo(trusted_by_ca=False) if tls else None,
    )


def test_entre_endpoints_iguais_ganha_o_cifrado() -> None:
    ok = [_ok(1883, "tcp", False), _ok(8883, "tcp", True)]
    escolhido = _escolher(ok, parse_broker_address("host"))
    assert escolhido.port == 8883


def test_mqtt_nativo_ganha_do_websocket() -> None:
    ok = [_ok(8084, "websockets", True), _ok(8883, "tcp", True)]
    assert _escolher(ok, parse_broker_address("host")).transport == "tcp"


def test_url_do_painel_nao_faz_o_websocket_sem_tls_vencer() -> None:
    # O caso real: colar http://host:18083 dizia "websocket sem TLS" no texto.
    ok = [_ok(8083, "websockets", False), _ok(8883, "tcp", True)]
    escolhido = _escolher(ok, parse_broker_address("http://host:18083"))
    assert (escolhido.port, escolhido.tls) == (8883, True)


def test_escolha_explicita_do_operador_vence() -> None:
    # Rede que bloqueia TLS: quem digitou ws://host:8083 quer aquilo mesmo.
    ok = [_ok(8083, "websockets", False), _ok(8883, "tcp", True)]
    escolhido = _escolher(ok, parse_broker_address("ws://host:8083"))
    assert (escolhido.port, escolhido.transport) == (8083, "websockets")
