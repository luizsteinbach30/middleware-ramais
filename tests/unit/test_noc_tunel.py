"""Túnel de acesso web do NOC (item 14 do docs/AGENTE-NOC.md, ADR 0007).

Três perguntas, cada uma com o seu grupo de testes:

- o destino é qualquer coisa que esta máquina alcança (IPv4, nome de host) ou um USCall do cadastro;
- os cabeçalhos atravessam sem quebrar a sessão do equipamento;
- uma requisição de verdade vai e volta pelo WebSocket, com corpo inteiro.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest
from websockets.asyncio.server import serve

from middleware_monitor.domain.noc import executor, tunel
from middleware_monitor.domain.noc.tunel import Destino, DestinoRecusado

# --- Destino ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip", ["10.1.2.3", "172.16.0.1", "192.168.0.20", "127.0.0.1", "169.254.1.1", "100.64.0.9", "8.8.8.8"]
)
def test_qualquer_ipv4_que_a_maquina_alcanca_passa(ip: str) -> None:
    # "Como se eu estivesse naquela máquina" (dono, 26/09): LAN, VPN, link-local, a própria
    # máquina e internet. Quem pode abrir é o NOC que decide.
    d = tunel.destino_da_lan(ip, 80, "http")
    assert d.base == f"http://{ip}"


def test_a_propria_interface_do_middleware_passa() -> None:
    assert tunel.destino_da_lan("127.0.0.1", 8080, "http").base == "http://127.0.0.1:8080"


@pytest.mark.parametrize("nome", ["pabx.loja.local", "USCALL.exemplo.com.br.", "impressora", "a-b.c"])
def test_nome_de_host_passa_sem_virar_ip(nome: str) -> None:
    d = tunel.destino_da_lan(nome, 443, "https")
    assert d.host == nome.lower().rstrip(".")  # conecta pelo nome: SNI e virtual host preservados


@pytest.mark.parametrize(
    "destino", ["0.0.0.0", "224.0.0.1", "255.255.255.255", "::1", "-x.local", "a b", "", "x" * 254, "a..b"]
)
def test_o_que_nao_e_equipamento_e_recusado(destino: str) -> None:
    with pytest.raises(DestinoRecusado):
        tunel.destino_da_lan(destino, 80, "http")


@pytest.mark.parametrize(("porta", "esquema"), [(0, "http"), (70000, "http"), (True, "http"), (80, "ftp")])
def test_porta_e_esquema_fora_de_forma(porta: object, esquema: str) -> None:
    with pytest.raises(DestinoRecusado):
        tunel.destino_da_lan("192.168.0.20", porta, esquema)


def test_uscall_vem_do_cadastro_e_so_do_cadastro() -> None:
    cadastro = [("Matriz", "https://uscall.exemplo.com.br:8443/"), ("Filial", "10.0.0.9")]
    assert tunel.destino_uscall("Matriz", cadastro).base == "https://uscall.exemplo.com.br:8443"
    assert tunel.destino_uscall("Filial", cadastro).base == "https://10.0.0.9"
    with pytest.raises(DestinoRecusado, match="Não há USCall"):
        tunel.destino_uscall("uscall.exemplo.com.br", cadastro)


# --- Cabeçalhos ------------------------------------------------------------------------------

_TEL = Destino("http", "192.168.0.20", 80, "http://192.168.0.20:80")


def test_pedido_troca_host_origin_e_referer_e_tira_os_de_salto() -> None:
    saida = dict(
        tunel.cabecalhos_do_pedido(
            [
                ["Host", "acesso-noc.workconnect.com.br"],
                ["Origin", "https://acesso-noc.workconnect.com.br"],
                ["Referer", "https://acesso-noc.workconnect.com.br/servlet?p=login&q=1"],
                ["Connection", "keep-alive"],
                ["Cookie", "JSESSIONID=abc"],
            ],
            _TEL,
        )
    )
    assert saida["Host"] == "192.168.0.20"
    assert saida["Origin"] == "http://192.168.0.20"
    assert saida["Referer"] == "http://192.168.0.20/servlet?p=login&q=1"
    assert saida["Cookie"] == "JSESSIONID=abc"
    assert "Connection" not in saida


def test_resposta_torna_location_relativo_e_tira_o_domain_do_cookie() -> None:
    saida = tunel.cabecalhos_da_resposta(
        [
            ("Location", "http://192.168.0.20/index.htm?x=1"),
            ("Set-Cookie", "sid=1; Domain=192.168.0.20; Path=/"),
            ("Strict-Transport-Security", "max-age=1"),
            ("Transfer-Encoding", "chunked"),
            ("Content-Type", "text/html"),
        ],
        _TEL,
    )
    assert ["Location", "/index.htm?x=1"] in saida
    assert ["Set-Cookie", "sid=1; Path=/"] in saida
    nomes = {k.lower() for k, _ in saida}
    assert "strict-transport-security" not in nomes
    assert "transfer-encoding" not in nomes


def test_location_de_outro_host_nao_e_mexido() -> None:
    saida = tunel.cabecalhos_da_resposta([("Location", "https://outro.com/x")], _TEL)
    assert saida == [["Location", "https://outro.com/x"]]


# --- Ponta a ponta: WebSocket + equipamento de mentira ----------------------------------------

_GRANDE = b"x" * (tunel.PEDACO * 2 + 10)


class _Equipamento(BaseHTTPRequestHandler):
    recebidos: ClassVar[list[tuple[str, str, dict[str, str], bytes]]] = []

    def _responder(self) -> None:
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        _Equipamento.recebidos.append((self.command, self.path, dict(self.headers), corpo))
        self.send_response(302 if self.path == "/login" else 200)
        if self.path == "/login":
            self.send_header("Location", f"http://{self.headers['Host']}/home")
            self.send_header("Set-Cookie", f"sid=42; Domain={self.headers['Host']}; Path=/")
        self.send_header("Content-Length", str(len(_GRANDE)))
        self.end_headers()
        self.wfile.write(_GRANDE)

    do_GET = do_POST = _responder  # noqa: N815 - nomes exigidos pelo http.server

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def equipamento():  # type: ignore[no-untyped-def]
    _Equipamento.recebidos = []
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Equipamento)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield servidor.server_address[1]
    servidor.shutdown()


def _frame(**campos: Any) -> str:
    return json.dumps(campos)


async def test_requisicao_vai_e_volta_pelo_websocket(equipamento: int) -> None:
    respostas: dict[int, dict[str, Any]] = {}
    pronto = asyncio.Event()
    cabecalhos_do_agente: dict[str, str] = {}

    async def noc(ws: Any) -> None:
        cabecalhos_do_agente["Authorization"] = ws.request.headers.get("Authorization", "")
        corpo = b"usuario=admin&senha=segredo"
        await ws.send(
            _frame(
                t="req",
                f=1,
                metodo="POST",
                caminho="/login",
                cabecalhos=[["Content-Type", "application/x-www-form-urlencoded"]],
            )
        )
        await ws.send(_frame(t="req.corpo", f=1, dados=base64.b64encode(corpo[:5]).decode()))
        await ws.send(_frame(t="req.corpo", f=1, dados=base64.b64encode(corpo[5:]).decode()))
        await ws.send(_frame(t="req.fim", f=1))
        await ws.send(_frame(t="req", f=2, metodo="GET", caminho="/status", cabecalhos=[]))
        await ws.send(_frame(t="req.fim", f=2))
        async for m in ws:
            frame = json.loads(m)
            r = respostas.setdefault(frame["f"], {"corpo": b""})
            if frame["t"] == "resp":
                r.update(status=frame["status"], cabecalhos=frame["cabecalhos"])
            elif frame["t"] == "resp.corpo":
                r["corpo"] += base64.b64decode(frame["dados"])
            elif frame["t"] == "resp.fim":
                r["fim"] = True
            if all(respostas.get(f, {}).get("fim") for f in (1, 2)):
                await ws.send(_frame(t="fechar"))
                pronto.set()

    async with serve(noc, "127.0.0.1", 0) as servidor:
        porta_ws = servidor.sockets[0].getsockname()[1]
        destino = Destino("http", "127.0.0.1", equipamento, "teste")
        canal = f"http://127.0.0.1:{porta_ws}"
        assert tunel.abrir("sessao-1", destino, canal=canal, credencial="ag_1.segredo", operador="noc:p@x")
        # A mesma sessão reentregue não abre de novo.
        assert not tunel.abrir(
            "sessao-1", destino, canal=canal, credencial="ag_1.segredo", operador="noc:p@x"
        )
        await asyncio.wait_for(pronto.wait(), timeout=10)
        for _ in range(50):
            if not tunel.abertas():
                break
            await asyncio.sleep(0.1)

    assert cabecalhos_do_agente["Authorization"] == "Bearer ag_1.segredo"
    assert tunel.abertas() == []
    login = respostas[1]
    assert login["status"] == 302
    cab = {k.lower(): v for k, v in login["cabecalhos"]}
    assert cab["location"] == "/home"
    assert cab["set-cookie"] == "sid=42; Path=/"
    assert login["corpo"] == _GRANDE
    assert respostas[2]["status"] == 200
    _metodo, caminho, cab_eq, corpo_eq = next(r for r in _Equipamento.recebidos if r[0] == "POST")
    assert (caminho, corpo_eq) == ("/login", b"usuario=admin&senha=segredo")
    # Corpo inteiro, com Content-Length: telefone não costuma aceitar chunked.
    assert cab_eq["Content-Length"] == str(len(corpo_eq))
    assert "Transfer-Encoding" not in cab_eq


# --- A tarefa ----------------------------------------------------------------------------------


def test_verbo_e_escrita_e_recusa_campo_de_rede() -> None:
    assert executor.ACOES["abrir_acesso_web"].raio == executor.ESCRITA
    assert executor.conferir("abrir_acesso_web", executor.ESCRITA, {"sessao": "s1"}) is not None
    ok = {"sessao": "s1", "tipoDeDestino": "lan", "destino": "192.168.0.20", "porta": 80, "esquema": "http"}
    assert executor.conferir("abrir_acesso_web", executor.ESCRITA, ok) is None
    recusa = executor.conferir(
        "abrir_acesso_web", executor.ESCRITA, {"sessao": "s1", "tipoDeDestino": "lan", "ip": "1.1.1.1"}
    )
    assert recusa is not None and "P_CODE_DE_REDE" in (recusa.erro or "")
    assert executor.conferir("abrir_acesso_web", executor.LEITURA, ok) is not None


async def test_tarefa_recusa_destino_fora_de_forma() -> None:
    ctx = executor.Contexto("t1", "pessoa@x", "http://127.0.0.1:1", "ag_1.s")
    r = await executor._abrir_acesso_web(
        {"sessao": "s2", "tipoDeDestino": "lan", "destino": "a b", "porta": 80, "esquema": "http"}, ctx
    )
    assert not r.ok and "nome de host" in (r.erro or "")
    assert tunel.abertas() == []
