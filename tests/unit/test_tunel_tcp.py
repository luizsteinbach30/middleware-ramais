"""Fluxo TCP do túnel (2.16.0, ADR 0010): o que leva o RDP e o SSH do guacd do NOC até o servidor.

Um servidor de eco em asyncio faz o papel do Windows/Linux do cliente, e um NOC de mentira fala
o protocolo v2 com os cabeçalhos novos (recursos e duração).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from websockets.asyncio.server import serve

from middleware_monitor.domain.noc import tunel
from middleware_monitor.domain.noc import tunel_protocolo as tp
from middleware_monitor.domain.noc.tunel import Destino

GRANDE = 600 * 1024


class _Noc:
    def __init__(self) -> None:
        self.json: list[dict[str, Any]] = []
        self.dados: dict[int, bytearray] = {}
        self.cabecalhos: dict[str, str] = {}
        self.duracao_na_sessao: int | None = None

    def achou(self, teste: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
        return next((x for x in self.json if teste(x)), None)


async def _esperar(teste: Callable[[], bool], prazo: float = 10) -> None:
    fim = time.monotonic() + prazo
    while not teste():
        assert time.monotonic() < fim, "não chegou a tempo"
        await asyncio.sleep(0.02)


async def _sessao(roteiro: Callable[[Any, _Noc], Any], destino: Destino, *, duracao: str = "28800") -> _Noc:
    noc = _Noc()
    pronto = asyncio.Event()

    def cabecalhos(_conexao: Any, _pedido: Any, resposta: Any) -> Any:
        resposta.headers[tp.CABECALHO_PROTOCOLO] = tp.PROTOCOLO
        resposta.headers[tp.CABECALHO_BANDA] = "0"
        resposta.headers[tp.CABECALHO_DURACAO] = duracao
        return resposta

    async def tratar(ws: Any) -> None:
        noc.cabecalhos = {k.lower(): v for k, v in ws.request.headers.raw_items()}

        async def ler() -> None:
            async for m in ws:
                if isinstance(m, bytes):
                    lido = tp.desempacotar(m)
                    assert lido is not None and lido[0] == tp.TCP_DADOS
                    noc.dados.setdefault(lido[1], bytearray()).extend(lido[2])
                else:
                    noc.json.append(json.loads(m))

        leitor = asyncio.create_task(ler())
        try:
            await roteiro(ws, noc)
            noc.duracao_na_sessao = next(iter(tunel._sessoes.values())).duracao_s
        finally:
            await ws.send(json.dumps({"t": "fechar"}))
            pronto.set()
            await asyncio.sleep(0.2)
            leitor.cancel()

    async with serve(tratar, "127.0.0.1", 0, process_response=cabecalhos) as servidor:
        porta_ws = servidor.sockets[0].getsockname()[1]
        canal = f"http://127.0.0.1:{porta_ws}"
        tunel.abrir(f"tcp{porta_ws}", destino, canal=canal, credencial="ag.x", operador="t")
        await asyncio.wait_for(pronto.wait(), timeout=30)
        for _ in range(50):
            if not tunel.abertas():
                break
            await asyncio.sleep(0.1)
    return noc


class _Servidor:
    """Eco; ``/grande`` manda 600 KiB; ``/fechar`` fecha do lado do servidor."""

    def __init__(self) -> None:
        self.recebido = bytearray()
        self.fim_do_cliente = asyncio.Event()

    async def atender(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        while True:
            dados = await leitor.read(65536)
            if not dados:
                self.fim_do_cliente.set()
                break
            self.recebido.extend(dados)
            if dados == b"/grande":
                escritor.write(b"x" * GRANDE)
            elif dados == b"/fechar":
                break
            else:
                escritor.write(dados)
            await escritor.drain()
        escritor.close()


async def _servidor() -> tuple[_Servidor, asyncio.Server, int]:
    s = _Servidor()
    srv = await asyncio.start_server(s.atender, "127.0.0.1", 0)
    return s, srv, srv.sockets[0].getsockname()[1]


def _dados(f: int, b: bytes) -> bytes:
    return tp.empacotar(tp.TCP_DADOS, f, b)


async def test_eco_nos_dois_sentidos_com_credito_depois_do_drain() -> None:
    s, srv, porta = await _servidor()

    async def roteiro(ws: Any, noc: _Noc) -> None:
        await ws.send(json.dumps({"t": "tcp.abrir", "f": 1}))
        await _esperar(lambda: noc.achou(lambda x: x["t"] == "tcp.aberto") is not None)
        await ws.send(_dados(1, b"ola rdp"))
        await _esperar(lambda: bytes(noc.dados.get(1, b"")) == b"ola rdp")
        await ws.send(json.dumps({"t": "tcp.fechar", "f": 1}))
        await asyncio.wait_for(s.fim_do_cliente.wait(), 5)

    async with srv:
        noc = await _sessao(roteiro, tunel.destino_tcp("127.0.0.1", porta))
    assert noc.cabecalhos.get(tp.CABECALHO_RECURSOS.lower()) == tp.RECURSOS  # o agente anuncia o TCP
    assert noc.duracao_na_sessao == 28800  # 8 h, vindo do NOC
    assert noc.achou(lambda x: x["t"] == "janela" and x["f"] == 1 and x["bytes"] == len(b"ola rdp"))
    assert bytes(s.recebido) == b"ola rdp"


async def test_janela_segura_o_que_sobe_ate_o_noc_dar_credito() -> None:
    _s, srv, porta = await _servidor()

    async def roteiro(ws: Any, noc: _Noc) -> None:
        await ws.send(json.dumps({"t": "tcp.abrir", "f": 7}))
        await _esperar(lambda: noc.achou(lambda x: x["t"] == "tcp.aberto") is not None)
        await ws.send(_dados(7, b"/grande"))
        await _esperar(lambda: len(noc.dados.get(7, b"")) >= tp.JANELA_INICIAL)
        await asyncio.sleep(0.5)
        noc.parado = len(noc.dados[7])  # type: ignore[attr-defined]
        await ws.send(json.dumps({"t": "janela", "f": 7, "bytes": GRANDE}))
        await _esperar(lambda: len(noc.dados.get(7, b"")) >= GRANDE)

    async with srv:
        noc = await _sessao(roteiro, tunel.destino_tcp("127.0.0.1", porta))
    assert tp.JANELA_INICIAL <= noc.parado < tp.JANELA_INICIAL + tunel.PEDACO_TCP  # type: ignore[attr-defined]
    assert bytes(noc.dados[7]) == b"x" * GRANDE


async def test_servidor_fecha_e_o_noc_fica_sabendo() -> None:
    _s, srv, porta = await _servidor()

    async def roteiro(ws: Any, noc: _Noc) -> None:
        await ws.send(json.dumps({"t": "tcp.abrir", "f": 2}))
        await _esperar(lambda: noc.achou(lambda x: x["t"] == "tcp.aberto") is not None)
        await ws.send(_dados(2, b"/fechar"))
        await _esperar(lambda: noc.achou(lambda x: x["t"] == "tcp.fechar" and x["f"] == 2) is not None)

    async with srv:
        await _sessao(roteiro, tunel.destino_tcp("127.0.0.1", porta))


async def test_recusas_sessao_web_e_servidor_fora() -> None:
    _s, srv, porta = await _servidor()
    srv.close()
    await srv.wait_closed()  # a porta agora não atende

    async def roteiro_fora(ws: Any, noc: _Noc) -> None:
        await ws.send(json.dumps({"t": "tcp.abrir", "f": 3}))
        await _esperar(lambda: noc.achou(lambda x: x["t"] == "erro") is not None)

    noc = await _sessao(roteiro_fora, tunel.destino_tcp("127.0.0.1", porta))
    assert "Não foi possível conectar" in noc.achou(lambda x: x["t"] == "erro")["mensagem"]  # type: ignore[index]

    async def roteiro_web(ws: Any, noc: _Noc) -> None:
        await ws.send(json.dumps({"t": "tcp.abrir", "f": 4}))
        await _esperar(lambda: noc.achou(lambda x: x["t"] == "erro") is not None)

    # Sessão aberta para a interface web não vira TCP para lugar nenhum.
    noc = await _sessao(roteiro_web, Destino("http", "127.0.0.1", porta, "web"))
    assert "não é de conexão TCP" in noc.achou(lambda x: x["t"] == "erro")["mensagem"]  # type: ignore[index]


def test_duracao_do_cabecalho() -> None:
    assert tp.duracao_do_cabecalho("28800", 3600) == 28800
    assert tp.duracao_do_cabecalho(None, 3600) == 3600
    assert tp.duracao_do_cabecalho("abc", 3600) == 3600
    assert tp.duracao_do_cabecalho("10", 3600) == 3600  # menos de 1 min: fora de forma
    assert tp.duracao_do_cabecalho("999999", 3600) == tp.DURACAO_TETO_S


def test_destino_tcp_segue_a_regra_do_acesso_web() -> None:
    d = tunel.destino_tcp("192.168.10.2", 3389)
    assert (d.esquema, d.host, d.porta, d.rotulo) == ("tcp", "192.168.10.2", 3389, "tcp://192.168.10.2:3389")
    for host, porta in (("224.0.0.1", 22), ("192.168.0.1", 0), ("a b", 22)):
        try:
            tunel.destino_tcp(host, porta)
        except tunel.DestinoRecusado:
            continue
        raise AssertionError((host, porta))
