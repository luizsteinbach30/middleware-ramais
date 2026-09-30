"""Sondas de porta e de página (AGENTE-NOC item 19) contra servidores descartáveis em 127.0.0.1.

Nada aqui sai da máquina: o servidor TCP é um socket escutando, e o HTTP é um
``http.server`` numa thread. O que se prova: porta aberta responde com latência,
porta fechada e tempo esgotado são falha com motivo, status errado é falha, e
**resposta vazia não é sucesso**.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from middleware_monitor.domain.equipamentos import sondas
from middleware_monitor.domain.equipamentos.lista import normalizar_parametros
from middleware_monitor.integrations.network.servicos import SondaHttp, SondaTcp, montar_url

# --- Servidores locais -------------------------------------------------------------------------


@pytest.fixture
def porta_aberta() -> Iterator[int]:
    """Socket escutando: o aperto de mão completa pelo backlog, sem precisar de accept."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    yield s.getsockname()[1]
    s.close()


@pytest.fixture
def porta_fechada() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    return porta


class _Pagina(BaseHTTPRequestHandler):
    metodos: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        _Pagina.metodos.append(self.command)
        if self.path == "/lento":
            time.sleep(1.5)
        status, corpo = {
            "/ok": (200, b"ola"),
            "/vazio": (200, b""),
            "/erro": (500, b"falhou"),
            "/semcorpo": (204, b""),
            "/lento": (200, b"tarde"),
            "/redireciona": (302, b""),
        }.get(self.path, (404, b"nao achei"))
        self.send_response(status)
        if status == 302:
            self.send_header("Location", "/ok")
        if status != 204:
            self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        if corpo:
            self.wfile.write(corpo)

    def log_message(self, *_a: object) -> None:  # silencia o stderr do teste
        return


@pytest.fixture
def servidor_http() -> Iterator[int]:
    _Pagina.metodos = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Pagina)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _http(porta: int, caminho: str, status: int = 200) -> dict:
    return normalizar_parametros("http", {"porta": porta, "caminho": caminho, "statusEsperado": status})


# --- TCP ------------------------------------------------------------------------------------------


async def test_tcp_porta_aberta_responde_com_latencia(porta_aberta) -> None:
    r = await SondaTcp().conectar("127.0.0.1", porta_aberta, 3.0)
    assert r.ok is True
    assert isinstance(r.valor, int) and r.valor >= 0
    assert r.erro is None


async def test_tcp_porta_fechada_e_falha_com_motivo(porta_fechada) -> None:
    r = await SondaTcp().conectar("127.0.0.1", porta_fechada, 5.0)
    assert r.ok is False and r.valor is None
    assert "fechada" in (r.erro or "") or "não respondeu" in (r.erro or "")


async def test_tcp_tempo_esgotado_e_falha(monkeypatch) -> None:
    async def nunca(*_a, **_k):
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio, "open_connection", nunca)
    inicio = time.perf_counter()
    r = await SondaTcp().conectar("127.0.0.1", 9, 0.2)
    assert r.ok is False
    assert "não respondeu em 0.2 s" in (r.erro or "")
    assert time.perf_counter() - inicio < 2


async def test_tcp_nome_que_nao_resolve_e_falha() -> None:
    r = await SondaTcp().conectar("nao-existe.invalid", 80, 3.0)
    assert r.ok is False and r.erro


# --- HTTP -----------------------------------------------------------------------------------------


async def test_http_status_esperado_com_corpo_e_ok(servidor_http) -> None:
    r = await SondaHttp().pedir(_http(servidor_http, "/ok"), "127.0.0.1", 3.0)
    assert r.ok is True and isinstance(r.valor, int)
    # Só leitura: um GET e nada mais.
    assert _Pagina.metodos == ["GET"]


async def test_http_status_errado_e_falha(servidor_http) -> None:
    r = await SondaHttp().pedir(_http(servidor_http, "/erro"), "127.0.0.1", 3.0)
    assert r.ok is False and r.valor is None
    assert r.erro == "HTTP 500, esperado 200"


async def test_http_200_com_resposta_vazia_nao_e_sucesso(servidor_http) -> None:
    r = await SondaHttp().pedir(_http(servidor_http, "/vazio"), "127.0.0.1", 3.0)
    assert r.ok is False
    assert "vazia" in (r.erro or "")


async def test_http_204_esperado_nao_precisa_de_corpo(servidor_http) -> None:
    r = await SondaHttp().pedir(_http(servidor_http, "/semcorpo", 204), "127.0.0.1", 3.0)
    assert r.ok is True


async def test_http_nao_segue_redirecionamento(servidor_http) -> None:
    errado = await SondaHttp().pedir(_http(servidor_http, "/redireciona"), "127.0.0.1", 3.0)
    assert errado.ok is False and errado.erro == "HTTP 302, esperado 200"
    certo = await SondaHttp().pedir(_http(servidor_http, "/redireciona", 302), "127.0.0.1", 3.0)
    assert certo.ok is True


async def test_http_tempo_esgotado_e_falha(servidor_http) -> None:
    r = await SondaHttp().pedir(_http(servidor_http, "/lento"), "127.0.0.1", 0.3)
    assert r.ok is False
    assert "não respondeu em 0.3 s" in (r.erro or "")


async def test_http_porta_fechada_e_falha(porta_fechada) -> None:
    r = await SondaHttp().pedir(_http(porta_fechada, "/ok"), "127.0.0.1", 5.0)
    assert r.ok is False and r.erro


def test_url_da_sonda() -> None:
    assert montar_url("10.0.0.5", normalizar_parametros("http", {})) == "http://10.0.0.5:80/"
    https = normalizar_parametros("http", {"esquema": "https", "caminho": "/login"})
    assert montar_url("nvr.loja", https) == "https://nvr.loja:443/login"
    assert https["verificarCertificado"] is False
    assert montar_url("fe80::1", normalizar_parametros("http", {"porta": 8080})) == "http://[fe80::1]:8080/"


# --- O despacho por tipo ------------------------------------------------------------------------


async def test_executar_despacha_tcp_e_http(porta_aberta, servidor_http) -> None:
    tcp = await sondas.executar("tcp", "127.0.0.1", {"porta": porta_aberta}, timeout_ping_ms=500)
    http = await sondas.executar("http", "127.0.0.1", _http(servidor_http, "/ok"), timeout_ping_ms=500)
    assert tcp.ok and http.ok


async def test_icmp_resolve_o_nome_antes_de_pingar(monkeypatch) -> None:
    pingados: list[str] = []

    class _Ping:
        async def ping(self, ip: str, timeout_ms: int) -> int | None:
            pingados.append(ip)
            return 7

    monkeypatch.setattr(sondas, "make_ping_probe", _Ping)
    r = await sondas.executar("icmp", "localhost", {}, timeout_ping_ms=500)
    assert r.ok is True and r.valor == 7
    assert pingados == ["127.0.0.1"]
    sem_nome = await sondas.executar("icmp", "nao-existe.invalid", {}, timeout_ping_ms=500)
    assert sem_nome.ok is False and "não resolve" in (sem_nome.erro or "")


async def test_sonda_que_quebra_vira_falha_e_nao_derruba_o_job(monkeypatch) -> None:
    class _Quebrada:
        async def conectar(self, *_a, **_k):
            raise RuntimeError("bug")

    monkeypatch.setattr(sondas, "make_tcp_probe", _Quebrada)
    r = await sondas.executar("tcp", "127.0.0.1", {"porta": 1}, timeout_ping_ms=500)
    assert r.ok is False and "RuntimeError" in (r.erro or "")
