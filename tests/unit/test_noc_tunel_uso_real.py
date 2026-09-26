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
