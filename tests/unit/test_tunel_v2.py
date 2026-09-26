"""Sessão do túnel no protocolo v2 (ADR 0009), contra um NOC de mentira que o anuncia.

Os testes antigos (``test_noc_tunel*.py``) rodam sem o cabeçalho: são a prova de que o agente
novo continua falando v1 com o NOC antigo.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from websockets.asyncio.server import serve

from middleware_monitor.domain.noc import tunel
from middleware_monitor.domain.noc import tunel_protocolo as tp
from middleware_monitor.domain.noc.tunel import Destino

GRANDE = 600 * 1024


class _Aparelho(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    nome = "a"

    def do_GET(self) -> None:
        if self.path == "/grande":
            corpo = b"x" * GRANDE
            tipo = "application/octet-stream"
        elif self.path == "/links":
            corpo = f'<a href="http://127.0.0.1:{self.server.server_address[1]}/menu">m</a> <img src="http://10.9.9.9/x.gif">'.encode()
            tipo = "text/html"
        else:
            corpo = f"{self.nome}:{self.path}".encode()
            tipo = "text/plain"
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *_: object) -> None:
        pass


def _servidor(nome: str) -> ThreadingHTTPServer:
    classe = type(f"Aparelho{nome}", (_Aparelho,), {"nome": nome})
    s = ThreadingHTTPServer(("127.0.0.1", 0), classe)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


@pytest.fixture
def aparelhos() -> Iterator[tuple[int, int]]:
    a, b = _servidor("a"), _servidor("b")
    yield a.server_address[1], b.server_address[1]
    a.shutdown()
    b.shutdown()


class _Noc:
    """O que o NOC de mentira recebeu, por fluxo."""

    def __init__(self) -> None:
        self.json: list[dict[str, Any]] = []
        self.corpo: dict[int, bytearray] = {}
        self.chegadas: dict[int, list[tuple[float, int]]] = {}
        self.ws_msgs: list[tuple[int, int, bytes]] = []
        self.inicio = time.monotonic()

    def binario(self, quadro: bytes) -> None:
        lido = tp.desempacotar(quadro)
        assert lido is not None, "quadro fora do contrato"
        tipo, f, dados = lido
        if tipo == tp.RESP_CORPO:
            self.corpo.setdefault(f, bytearray()).extend(dados)
            self.chegadas.setdefault(f, []).append((time.monotonic() - self.inicio, len(dados)))
        else:
            self.ws_msgs.append((tipo, f, dados))

    def achou(self, teste: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
        return next((x for x in self.json if teste(x)), None)


async def _sessao(
    roteiro: Callable[[Any, _Noc], Any], porta_sessao: int, *, banda: int = 0, prazo: float = 20
) -> _Noc:
    noc = _Noc()
    pronto = asyncio.Event()

    def cabecalhos(_conexao: Any, _pedido: Any, resposta: Any) -> Any:
        resposta.headers[tp.CABECALHO_PROTOCOLO] = tp.PROTOCOLO
        resposta.headers[tp.CABECALHO_BANDA] = str(banda)
        return resposta

    async def tratar(ws: Any) -> None:
        assert ws.request.headers.get(tp.CABECALHO_PROTOCOLO) == tp.PROTOCOLO  # o agente pediu a v2

        async def ler() -> None:
            async for m in ws:
                if isinstance(m, bytes):
                    noc.binario(m)
                else:
                    noc.json.append(json.loads(m))

        leitor = asyncio.create_task(ler())
        try:
            await roteiro(ws, noc)
        finally:
            await ws.send(json.dumps({"t": "fechar"}))
            pronto.set()
            await asyncio.sleep(0.2)
            leitor.cancel()

    async with serve(tratar, "127.0.0.1", 0, process_response=cabecalhos) as servidor:
        porta_ws = servidor.sockets[0].getsockname()[1]
        destino = Destino("http", "127.0.0.1", porta_sessao, "teste")
        tunel.abrir(
            f"v2{porta_ws}", destino, canal=f"http://127.0.0.1:{porta_ws}", credencial="ag.x", operador="t"
        )
        await asyncio.wait_for(pronto.wait(), timeout=prazo)
        for _ in range(50):
            if not tunel.abertas():
                break
            await asyncio.sleep(0.1)
    return noc


async def _esperar(teste: Callable[[], bool], prazo: float = 10) -> None:
    fim = time.monotonic() + prazo
    while not teste():
        assert time.monotonic() < fim, "não chegou a tempo"
        await asyncio.sleep(0.02)


def _req(f: int, caminho: str, origem: dict[str, Any] | None = None) -> str:
    frame: dict[str, Any] = {"t": "req", "f": f, "metodo": "GET", "caminho": caminho, "cabecalhos": []}
    if origem:
        frame["origem"] = origem
    return json.dumps(frame)


async def test_corpo_binario_e_janela_por_fluxo(aparelhos: tuple[int, int]) -> None:
    async def roteiro(ws: Any, noc: _Noc) -> None:
        await ws.send(_req(1, "/grande"))
        await ws.send(json.dumps({"t": "req.fim", "f": 1}))
        await _esperar(lambda: len(noc.corpo.get(1, b"")) >= tp.JANELA_INICIAL)
        await asyncio.sleep(0.5)
        # Sem crédito novo, o agente parou na janela: o resto do download espera.
        noc.parado = len(noc.corpo[1])  # type: ignore[attr-defined]
        await ws.send(json.dumps({"t": "janela", "f": 1, "bytes": GRANDE}))
        await _esperar(lambda: noc.achou(lambda x: x["t"] == "resp.fim" and x["f"] == 1) is not None)

    noc = await _sessao(roteiro, aparelhos[0])
    assert noc.achou(lambda x: x["t"] == "resp" and x["f"] == 1)["status"] == 200  # type: ignore[index]
    assert tp.JANELA_INICIAL <= noc.parado < tp.JANELA_INICIAL + tunel.PEDACO  # type: ignore[attr-defined]
    assert bytes(noc.corpo[1]) == b"x" * GRANDE  # tudo, em binário, sem base64
    assert not [x for x in noc.json if x["t"] == "resp.corpo"]


async def test_balde_limita_a_banda_da_sessao(aparelhos: tuple[int, int]) -> None:
    async def roteiro(ws: Any, noc: _Noc) -> None:
        await ws.send(_req(1, "/grande"))
        await ws.send(json.dumps({"t": "req.fim", "f": 1}))
        await ws.send(json.dumps({"t": "janela", "f": 1, "bytes": GRANDE}))
        await _esperar(lambda: noc.achou(lambda x: x["t"] == "resp.fim") is not None, prazo=30)

    noc = await _sessao(roteiro, aparelhos[0], banda=2048, prazo=40)  # 2 Mbit/s = 256 KB/s
    tempos = noc.chegadas[1]
    duracao = tempos[-1][0] - tempos[0][0]
    # 600 KB a 256 KB/s, com rajada de 1 s (256 KB): ~1,34 s. Sem o balde seria instantâneo.
    assert duracao >= 1.15, duracao


async def test_pedido_para_outra_origem_da_sessao(aparelhos: tuple[int, int]) -> None:
    sessao, outra = aparelhos

    async def roteiro(ws: Any, noc: _Noc) -> None:
        await ws.send(_req(1, "/x"))
        await ws.send(json.dumps({"t": "req.fim", "f": 1}))
        await ws.send(_req(2, "/y", {"esquema": "http", "host": "127.0.0.1", "porta": outra}))
        await ws.send(json.dumps({"t": "req.fim", "f": 2}))
        await ws.send(_req(3, "/z", {"esquema": "ftp", "host": "127.0.0.1", "porta": 21}))
        await _esperar(lambda: len([x for x in noc.json if x["t"] in {"resp.fim", "erro"}]) >= 3)

    noc = await _sessao(roteiro, sessao)
    assert bytes(noc.corpo[1]) == b"a:/x"
    assert bytes(noc.corpo[2]) == b"b:/y"  # a outra origem, pela mesma sessão
    assert "esquema" in noc.achou(lambda x: x["t"] == "erro" and x["f"] == 3)["mensagem"]  # type: ignore[index]


async def test_links_da_pagina_vao_para_as_origens(aparelhos: tuple[int, int]) -> None:
    async def roteiro(ws: Any, noc: _Noc) -> None:
        await ws.send(_req(1, "/links"))
        await ws.send(json.dumps({"t": "req.fim", "f": 1}))
        await _esperar(lambda: noc.achou(lambda x: x["t"] == "resp.fim") is not None)

    noc = await _sessao(roteiro, aparelhos[0])
    pagina = bytes(noc.corpo[1])
    assert b'href="/menu"' in pagina
    assert b'src="/__tunel/ir?u=http%3A%2F%2F10.9.9.9%2Fx.gif"' in pagina


async def test_websocket_do_aparelho_vai_e_volta(aparelhos: tuple[int, int]) -> None:
    async def eco(conexao: Any) -> None:
        async for m in conexao:
            await conexao.send(m)

    async with serve(eco, "127.0.0.1", 0, subprotocols=["status"]) as aparelho_ws:  # type: ignore[list-item]
        porta = aparelho_ws.sockets[0].getsockname()[1]

        async def roteiro(ws: Any, noc: _Noc) -> None:
            await ws.send(
                json.dumps(
                    {
                        "t": "ws.abrir",
                        "f": 7,
                        "caminho": "/ao-vivo",
                        "cabecalhos": [],
                        "protocolos": ["status"],
                    }
                )
            )
            await _esperar(lambda: noc.achou(lambda x: x["t"] == "ws.aberto") is not None)
            await ws.send(tp.empacotar(tp.WS_TEXTO, 7, "olá".encode()))
            await ws.send(tp.empacotar(tp.WS_BINARIO, 7, b"\x00\x01"))
            await _esperar(lambda: len(noc.ws_msgs) >= 2)
            await ws.send(json.dumps({"t": "ws.fechar", "f": 7, "codigo": 1000}))
            await _esperar(lambda: noc.achou(lambda x: x["t"] == "ws.fechar") is not None)

        noc = await _sessao(roteiro, porta)
    assert noc.achou(lambda x: x["t"] == "ws.aberto")["protocolo"] == "status"  # type: ignore[index]
    assert (tp.WS_TEXTO, 7, "olá".encode()) in noc.ws_msgs
    assert (tp.WS_BINARIO, 7, b"\x00\x01") in noc.ws_msgs
