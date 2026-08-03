"""Guarda de instância única do .exe desktop (v2.7.1).

Regressão da v2.7.0 em campo: o `.exe` é empacotado com `console=False`, então
quando a porta 8080 já estava ocupada (processo órfão da versão anterior ou o
serviço NSSM) o uvicorn falhava com `[Errno 10048]` num stderr invisível e o
app "não iniciava", sem nenhuma mensagem.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from middleware_monitor.desktop import (
    _ocupante_e_o_middleware,
    _port_ocupada,
    _porta_ocupada_com_retry,
)


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_porta_livre_nao_e_detectada_como_ocupada() -> None:
    assert _port_ocupada(_porta_livre(), timeout=0.5) is False


def test_porta_com_listener_e_detectada() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    porta = int(srv.getsockname()[1])
    try:
        assert _port_ocupada(porta, timeout=0.5) is True
    finally:
        srv.close()


@pytest.fixture
def servidor_falso():
    """Sobe um HTTP mínimo e devolve (porta, setter do corpo de /healthz)."""
    estado = {"corpo": b'{"status":"ok"}', "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # assinatura exigida pela stdlib
            self.send_response(estado["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(estado["corpo"])

        def log_message(self, *_args):  # silencia o log no stderr
            return

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    porta = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield porta, estado
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_reconhece_o_proprio_middleware_na_porta(servidor_falso) -> None:
    porta, _estado = servidor_falso
    assert _ocupante_e_o_middleware(porta) is True


def test_outro_programa_na_porta_nao_e_confundido(servidor_falso) -> None:
    """Um serviço qualquer respondendo na 8080 não pode virar "já rodando" —
    a orientação ao usuário é outra (liberar a porta, não abrir o painel)."""
    porta, estado = servidor_falso
    estado["corpo"] = b"<html>Servidor de outra coisa</html>"
    assert _ocupante_e_o_middleware(porta) is False


def test_porta_muda_sem_ninguem_escutando() -> None:
    """Nada escutando: `_ocupante_e_o_middleware` não pode explodir."""
    assert _ocupante_e_o_middleware(_porta_livre(), timeout=0.5) is False


def test_retry_desiste_apenas_se_a_porta_seguir_ocupada(monkeypatch) -> None:
    """Logo após um update o processo antigo pode estar encerrando: uma
    leitura isolada não pode condenar a inicialização."""
    leituras = iter([True, False])
    monkeypatch.setattr(
        "middleware_monitor.desktop._port_ocupada", lambda *_a, **_k: next(leituras),
    )
    monkeypatch.setattr("middleware_monitor.desktop.time.sleep", lambda _s: None)
    assert _porta_ocupada_com_retry(1234, tentativas=3) is False


def test_retry_confirma_ocupacao_persistente(monkeypatch) -> None:
    monkeypatch.setattr(
        "middleware_monitor.desktop._port_ocupada", lambda *_a, **_k: True,
    )
    monkeypatch.setattr("middleware_monitor.desktop.time.sleep", lambda _s: None)
    assert _porta_ocupada_com_retry(1234, tentativas=3) is True
