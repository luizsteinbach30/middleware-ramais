"""O túnel sob uso de verdade (25/09): "começa bem e trava" e "abriu o link local".

Cada teste reproduz o que o dono viu com os telefones e o USCall:

- a página de status ao vivo chega em pedacinhos — e ficava presa esperando juntar 64 KB;
- a página carrega dezenas de arquivos de uma vez — e o telefone engasgava com 16 conexões;
- o firmware escreve ``http://<ip>/...`` na página — e o clique saía do túnel;
- o aparelho que só fala https redireciona o http — e isso virava um laço.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from websockets.asyncio.server import serve

from middleware_monitor.domain.noc import tunel
from middleware_monitor.domain.noc.tunel import Destino

TEL = Destino("http", "192.168.0.20", 80, "http://192.168.0.20:80")


# --- Reescrita de links -----------------------------------------------------------------------


def test_links_absolutos_do_equipamento_viram_caminho() -> None:
    html = (
        b'<a href="http://192.168.0.20/cgi-bin/x?a=1">x</a>'
        b"<script>location.href='http://192.168.0.20:80/index.htm';</script>"
        b'<img src="//192.168.0.20/logo.png">'
        b'<meta http-equiv="refresh" content="0;url=http://192.168.0.20">'
        b'{"u":"http:\\/\\/192.168.0.20\\/api\\/s"}'
    )
    saida = tunel.reescrever_links(html, TEL)
    assert b'href="/cgi-bin/x?a=1"' in saida
    assert b"location.href='/index.htm'" in saida
    assert b'src="/logo.png"' in saida
    assert b'url=/">' in saida
    assert b'"u":"\\/api\\/s"' in saida
    assert b"192.168.0.20" not in saida


def test_o_que_nao_e_link_do_equipamento_fica() -> None:
    texto = (
        b'<input value="192.168.0.20">'  # o IP num campo de configuração
        b'<a href="http://192.168.0.200/">outro</a>'  # outro aparelho, prefixo igual
        b'<a href="https://ajuda.fabricante.com/">ajuda</a>'
    )
    assert tunel.reescrever_links(texto, TEL) == texto


def test_uscall_por_nome() -> None:
    us = Destino("https", "uscall.exemplo.com.br", 443, "uscall:Matriz")
    saida = tunel.reescrever_links(b'fetch("https://uscall.exemplo.com.br/api/extenstatus")', us)
    assert saida == b'fetch("/api/extenstatus")'


def test_fluxo_continuo_nao_e_reescrito() -> None:
    assert tunel.reescreve("text/html; charset=utf-8")
    assert tunel.reescreve("application/javascript")
    assert not tunel.reescreve("text/event-stream")
    assert not tunel.reescreve("image/png")


# --- Redirecionamento para o próprio aparelho --------------------------------------------------


def test_http_que_manda_para_https_troca_o_destino() -> None:
    novo = tunel.para_onde("https://192.168.0.20/login", TEL)
    assert novo is not None and novo.base == "https://192.168.0.20"
    assert tunel.para_onde("http://192.168.0.20/login", TEL) is None  # mesmo destino
    assert tunel.para_onde("https://outro.com/", TEL) is None
    # E o Location vira caminho, qualquer que seja o esquema.
    assert tunel.cabecalhos_da_resposta([("Location", "https://192.168.0.20:443/login")], TEL) == [
        ["Location", "/login"]
    ]


# --- Sob carga: o equipamento de mentira ------------------------------------------------------------


class _Aparelho(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    simultaneas = 0
    maximo = 0
    trava = threading.Lock()

    def do_GET(self) -> None:
        with _Aparelho.trava:
            _Aparelho.simultaneas += 1
            _Aparelho.maximo = max(_Aparelho.maximo, _Aparelho.simultaneas)
        try:
            if self.path == "/ao-vivo":
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"5\r\nlinha\r\n")
                self.wfile.flush()
                time.sleep(3)  # a próxima linha demora: a primeira tem de ter chegado antes
                self.wfile.write(b"0\r\n\r\n")
                return
            time.sleep(0.3)  # arquivo lento: as requisições se sobrepõem
            corpo = self.path.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        finally:
            with _Aparelho.trava:
                _Aparelho.simultaneas -= 1

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def aparelho():  # type: ignore[no-untyped-def]
    _Aparelho.simultaneas = _Aparelho.maximo = 0
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Aparelho)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield servidor.server_address[1]
    servidor.shutdown()


async def _sessao(porta_aparelho: int, pedidos: list[dict[str, Any]], ate: Any) -> dict[int, dict[str, Any]]:
    """Um NOC de mentira que manda ``pedidos`` e junta as respostas até ``ate(respostas)``."""
    respostas: dict[int, dict[str, Any]] = {}
    pronto = asyncio.Event()
    inicio = time.monotonic()

    async def noc(ws: Any) -> None:
        for p in pedidos:
            await ws.send(json.dumps({"t": "req", "cabecalhos": [], **p}))
            await ws.send(json.dumps({"t": "req.fim", "f": p["f"]}))
        async for m in ws:
            frame = json.loads(m)
            r = respostas.setdefault(frame["f"], {"corpo": b"", "pedacos": []})
            if frame["t"] == "resp":
                r["status"] = frame["status"]
            elif frame["t"] == "resp.corpo":
                r["corpo"] += base64.b64decode(frame["dados"])
                r["pedacos"].append(time.monotonic() - inicio)
            elif frame["t"] == "resp.fim":
                r["fim"] = time.monotonic() - inicio
            elif frame["t"] == "erro":
                r["erro"] = frame["mensagem"]
            if ate(respostas):
                await ws.send(json.dumps({"t": "fechar"}))
                pronto.set()

    async with serve(noc, "127.0.0.1", 0) as servidor:
        porta_ws = servidor.sockets[0].getsockname()[1]
        destino = Destino("http", "127.0.0.1", porta_aparelho, "teste")
        tunel.abrir(
            f"s{porta_ws}", destino, canal=f"http://127.0.0.1:{porta_ws}", credencial="ag.x", operador="t"
        )
        await asyncio.wait_for(pronto.wait(), timeout=30)
        for _ in range(50):
            if not tunel.abertas():
                break
            await asyncio.sleep(0.1)
    return respostas


async def test_pedaco_ao_vivo_sai_na_hora(aparelho: int) -> None:
    r = await _sessao(
        aparelho, [{"f": 1, "metodo": "GET", "caminho": "/ao-vivo"}], lambda rs: rs.get(1, {}).get("fim")
    )
    ao_vivo = r[1]
    assert ao_vivo["corpo"] == b"linha"
    # A primeira linha chegou antes de o aparelho terminar (3 s depois).
    assert ao_vivo["pedacos"][0] < ao_vivo["fim"] - 2


async def test_vinte_requisicoes_de_uma_vez_seis_conexoes_e_nenhuma_perdida(aparelho: int) -> None:
    pedidos = [{"f": i, "metodo": "GET", "caminho": f"/arquivo-{i}.js"} for i in range(1, 21)]
    r = await _sessao(
        aparelho, pedidos, lambda rs: sum(1 for x in rs.values() if "fim" in x or "erro" in x) == 20
    )
    assert not [x for x in r.values() if "erro" in x], "nenhuma requisição pode virar erro"
    assert all(r[i]["corpo"] == f"/arquivo-{i}.js".encode() for i in range(1, 21))
    assert _Aparelho.maximo <= tunel.CONEXOES_COM_O_EQUIPAMENTO


# --- 26/09: "os menus dos telefones ainda não carregam por inteiro" ---------------------------------


class _FechaAConexaoParada:
    """Servidor embarcado de verdade: atende o primeiro pedido com keep-alive e, no pedido
    seguinte da mesma conexão, lê e fecha sem responder — a corrida de quem fecha a conexão
    parada enquanto o próximo pedido já estava a caminho."""

    def __init__(self, *, um_por_conexao: bool = False) -> None:
        # um_por_conexao: toda conexão atende um pedido só (Boa/GoAhead com limite de 1).
        self.um_por_conexao = um_por_conexao
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.porta = self.sock.getsockname()[1]
        self.conexoes = 0
        self.pedidos: list[tuple[int, str, dict[str, str], bytes]] = []
        threading.Thread(target=self._aceitar, daemon=True).start()

    def _aceitar(self) -> None:
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conexoes += 1
            threading.Thread(target=self._atender, args=(c, self.conexoes), daemon=True).start()

    def _atender(self, c: socket.socket, n: int) -> None:
        arquivo = c.makefile("rb")
        atendidos = 0
        with c:
            while True:
                linha = arquivo.readline()
                if not linha:
                    return
                cabecalhos: dict[str, str] = {}
                while (h := arquivo.readline()) not in (b"\r\n", b""):
                    k, _, v = h.decode().partition(":")
                    cabecalhos[k.strip().lower()] = v.strip()
                corpo = arquivo.read(int(cabecalhos.get("content-length", "0")))
                if atendidos == 1 and (n == 1 or self.um_por_conexao):
                    return  # fecha sem responder
                self.pedidos.append((n, linha.decode().split()[0], cabecalhos, corpo))
                resposta = b"ok " + linha.split()[0]
                c.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: keep-alive\r\n"
                    b"Content-Length: " + str(len(resposta)).encode() + b"\r\n\r\n" + resposta
                )
                atendidos += 1

    def fechar(self) -> None:
        self.sock.close()


async def test_post_na_conexao_que_o_aparelho_fechou_vai_de_novo() -> None:
    aparelho = _FechaAConexaoParada()
    respostas: dict[int, dict[str, Any]] = {}
    pronto = asyncio.Event()

    async def noc(ws: Any) -> None:
        await ws.send(json.dumps({"t": "req", "f": 1, "metodo": "GET", "caminho": "/menu", "cabecalhos": []}))
        await ws.send(json.dumps({"t": "req.fim", "f": 1}))
        async for m in ws:
            frame = json.loads(m)
            r = respostas.setdefault(frame["f"], {"corpo": b""})
            if frame["t"] == "resp":
                r["status"] = frame["status"]
            elif frame["t"] == "resp.corpo":
                r["corpo"] += base64.b64decode(frame["dados"])
            elif frame["t"] == "erro":
                r["erro"] = frame["mensagem"]
            elif frame["t"] == "resp.fim" and frame["f"] == 1:
                # O GET deixou a conexão no pool; o POST do menu vai nela.
                corpo = base64.b64encode(b"acao=listar").decode()
                await ws.send(
                    json.dumps(
                        {
                            "t": "req",
                            "f": 2,
                            "metodo": "POST",
                            "caminho": "/cgi-bin/menu",
                            "cabecalhos": [["Content-Type", "application/x-www-form-urlencoded"]],
                        }
                    )
                )
                await ws.send(json.dumps({"t": "req.corpo", "f": 2, "dados": corpo}))
                await ws.send(json.dumps({"t": "req.fim", "f": 2}))
            if frame["t"] in {"resp.fim", "erro"} and frame["f"] == 2:
                await ws.send(json.dumps({"t": "fechar"}))
                pronto.set()

    try:
        async with serve(noc, "127.0.0.1", 0) as servidor:
            porta_ws = servidor.sockets[0].getsockname()[1]
            destino = Destino("http", "127.0.0.1", aparelho.porta, "teste")
            tunel.abrir(
                f"s{porta_ws}", destino, canal=f"http://127.0.0.1:{porta_ws}", credencial="ag.x", operador="t"
            )
            await asyncio.wait_for(pronto.wait(), timeout=30)
            for _ in range(50):
                if not tunel.abertas():
                    break
                await asyncio.sleep(0.1)
    finally:
        aparelho.fechar()

    assert "erro" not in respostas[2], respostas[2].get("erro")
    assert respostas[2]["status"] == 200 and respostas[2]["corpo"] == b"ok POST"
    post = [p for p in aparelho.pedidos if p[1] == "POST"]
    assert len(post) == 1 and post[0][0] == 2  # uma vez só, numa conexão nova
    assert post[0][3] == b"acao=listar"  # o mesmo corpo
    # Todo pedido leva a marca do túnel (o login do middleware separa as tentativas por ela).
    assert all(p[2].get("x-noc-tunel") == f"s{porta_ws}" for p in aparelho.pedidos)


async def test_equipamento_que_atende_um_pedido_por_conexao_carrega_o_menu_inteiro() -> None:
    """Medido no Chrome (26/09): repetir pelo pool deixava 2 de 22 pedidos em 502. As imagens
    abrem várias conexões ao mesmo tempo, que ficam paradas no pool; o POST seguinte cai numa
    delas, é derrubado, e a repetição caía em OUTRA conexão velha. Agora a repetição vai por
    conexão nova e a sessão para de reaproveitar conexão com esse equipamento."""
    aparelho = _FechaAConexaoParada(um_por_conexao=True)
    simultaneos, total = 6, 16
    respostas: dict[int, dict[str, Any]] = {}
    pronto = asyncio.Event()

    async def noc(ws: Any) -> None:
        async def pedir(f: int) -> None:
            metodo = "POST" if f > simultaneos else "GET"
            cab = {"t": "req", "f": f, "metodo": metodo, "caminho": f"/menu/{f}", "cabecalhos": []}
            await ws.send(json.dumps(cab))
            if metodo == "POST":
                corpo = base64.b64encode(b"x=1").decode()
                await ws.send(json.dumps({"t": "req.corpo", "f": f, "dados": corpo}))
            await ws.send(json.dumps({"t": "req.fim", "f": f}))

        for f in range(1, simultaneos + 1):  # enchem o pool de conexões que o aparelho não reaproveita
            await pedir(f)
        prontos = 0
        async for m in ws:
            frame = json.loads(m)
            r = respostas.setdefault(frame["f"], {})
            if frame["t"] == "resp":
                r["status"] = frame["status"]
            elif frame["t"] == "erro":
                r["erro"] = frame["mensagem"]
            if frame["t"] not in {"resp.fim", "erro"}:
                continue
            prontos += 1
            if prontos == total:
                await ws.send(json.dumps({"t": "fechar"}))
                pronto.set()
            elif prontos >= simultaneos:
                await asyncio.sleep(0.05)
                await pedir(prontos + 1)  # os POSTs do menu, um depois do outro

    try:
        async with serve(noc, "127.0.0.1", 0) as servidor:
            porta_ws = servidor.sockets[0].getsockname()[1]
            destino = Destino("http", "127.0.0.1", aparelho.porta, "teste")
            tunel.abrir(
                f"s{porta_ws}", destino, canal=f"http://127.0.0.1:{porta_ws}", credencial="ag.x", operador="t"
            )
            await asyncio.wait_for(pronto.wait(), timeout=30)
            for _ in range(50):
                if not tunel.abertas():
                    break
                await asyncio.sleep(0.1)
    finally:
        aparelho.fechar()

    falhas = {f: r for f, r in respostas.items() if r.get("status") != 200}
    assert falhas == {}, falhas
    assert len(aparelho.pedidos) == total  # cada pedido chegou uma vez só ao equipamento
