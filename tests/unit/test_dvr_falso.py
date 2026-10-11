"""O DVR falso do laboratório do acesso ao DVR (ADR 0043 do NOC): página, bloqueio e RTSP.

O simulador é ferramenta de laboratório, mas é com ele que se prova que o NOC tenta a senha uma vez só
e identifica o DVR pela página — então o comportamento dele é testado aqui.
"""

from __future__ import annotations

import hashlib
import re
import socket
import threading
import urllib.error
import urllib.request

import pytest

from tools.simuladores import intelbras as sim_intelbras
from tools.simuladores.rtsp import FrenteRtsp, traduzir


def _md5(t: str) -> str:
    return hashlib.md5(t.encode()).hexdigest()  # é o algoritmo do digest


def _livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def dvr():
    sim = sim_intelbras.SimuladorIntelbras("admin", "certa")
    porta = _livre()
    servidor = sim_intelbras.montar_servidor(sim, porta)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield sim, f"http://127.0.0.1:{porta}"
    servidor.shutdown()
    servidor.server_close()


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:  # laboratório local
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def test_plugin_data_e_pagina_sem_senha_por_variante(dvr) -> None:
    sim, base = dvr
    status, corpo = _get(f"{base}/plugin.data")
    assert status == 200 and "PluginName=WebActiveX.Plugin.4.1.511.0" in corpo
    status, corpo = _get(f"{base}/")
    assert status == 200
    assert 'PLUGINS_NAME = "WebActiveX.Plugin.4.1.511.0"' in corpo and "classid=\"clsid:9A21544E" in corpo
    sim.mudar({"pagina": "moderno"})
    assert _get(f"{base}/plugin.data")[0] == 404
    assert "RPC2_Login" in _get(f"{base}/")[1]
    sim.mudar({"pagina": "desconhecido"})
    assert "FabricanteX.Plugin" in _get(f"{base}/plugin.data")[1]


def _cabecalho(sim, metodo: str, uri: str, senha: str) -> str:
    desafio = sim.desafio()
    realm = re.search(r'realm="([^"]+)"', desafio).group(1)
    nonce = re.search(r'nonce="([^"]+)"', desafio).group(1)
    resp = _md5(f"{_md5(f'admin:{realm}:{senha}')}:{nonce}:{_md5(f'{metodo}:{uri}')}")
    return f'Digest username="admin", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}"'


def test_bloqueia_depois_de_cinco_senhas_erradas_e_a_certa_nao_entra_mais(dvr) -> None:
    sim, _ = dvr
    assert sim.conferir_login("GET", None) is False  # o desafio não conta
    assert sim.ler()["falhas"] == 0
    for _ in range(4):
        assert sim.conferir_login("GET", _cabecalho(sim, "GET", "/x", "errada")) is False
    assert not sim.bloqueado()
    assert sim.conferir_login("GET", _cabecalho(sim, "GET", "/x", "certa")) is True  # zera
    for _ in range(5):
        sim.conferir_login("GET", _cabecalho(sim, "GET", "/x", "errada"))
    assert sim.bloqueado()
    assert sim.conferir_login("GET", _cabecalho(sim, "GET", "/x", "certa")) is False
    sim.reiniciar()
    assert not sim.bloqueado() and sim.ler()["pagina"] == "legado-4"


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("rtsp://10.99.1.108:554/cam/realmonitor?channel=3&subtype=1", "/ch3s1"),
        ("rtsp://10.99.1.108:554/cam/realmonitor?channel=1&subtype=0", "/ch1s0"),
        (
            "rtsp://10.99.1.108/cam/playback?channel=2&starttime=2026_10_09_08_00_00&endtime=2026_10_09_09_00_00",
            "/pb2",
        ),
        ("rtsp://10.99.1.108/Streaming/Channels/1201", "/ch12s0"),
        ("rtsp://10.99.1.108/Streaming/Channels/302", "/ch3s1"),
        ("rtsp://10.99.1.108/Streaming/tracks/101?starttime=20261009T080000Z", "/pb1"),
        ("rtsp://127.0.0.1:8554/ch1s1/trackID=0", "/ch1s1/trackID=0"),
        ("rtsp://10.99.1.108/live/main", None),
    ],
)
def test_traduz_os_caminhos_de_verdade(url: str, esperado: str | None) -> None:
    assert traduzir(url) == esperado


def _mediamtx_falso() -> tuple[int, list[bytes]]:
    """Responde 200 com um SDP a qualquer pedido e guarda o que recebeu."""
    recebidos: list[bytes] = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()

    def atender() -> None:
        c, _ = srv.accept()
        with c:
            while True:
                try:
                    d = c.recv(65536)
                except OSError:
                    return
                if not d:
                    return
                recebidos.append(d)
                cseq = re.search(rb"CSeq: (\d+)", d).group(1).decode()
                sdp = "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
                cabeca = f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Length: {len(sdp)}\r\n\r\n"
                c.sendall(f"{cabeca}{sdp}".encode())

    threading.Thread(target=atender, daemon=True).start()
    return int(srv.getsockname()[1]), recebidos


def _rtsp(s: socket.socket, texto: str) -> str:
    s.sendall(texto.encode())
    s.settimeout(5)
    resposta = b""
    while b"\r\n\r\n" not in resposta:
        resposta += s.recv(65536)
    cab, _, corpo = resposta.partition(b"\r\n\r\n")
    m = re.search(rb"Content-Length: (\d+)", cab)
    while m and len(corpo) < int(m.group(1)):
        corpo += s.recv(65536)
    return (cab + b"\r\n\r\n" + corpo).decode()


def test_rtsp_pede_digest_conta_a_tentativa_e_traduz_para_o_mediamtx() -> None:
    sim = sim_intelbras.SimuladorIntelbras("admin", "certa")
    porta_mtx, recebidos = _mediamtx_falso()
    porta = _livre()
    frente = FrenteRtsp(sim, "127.0.0.1", porta, ("127.0.0.1", porta_mtx))
    threading.Thread(target=frente.servir, daemon=True).start()
    url = "rtsp://10.99.1.108:554/cam/realmonitor?channel=2&subtype=1"
    with socket.create_connection(("127.0.0.1", porta)) as s:
        r = _rtsp(s, f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 1\r\n\r\n")
        assert r.startswith("RTSP/1.0 401") and "WWW-Authenticate: Digest" in r
        assert sim.ler()["tentativasRtsp"] == 0  # o desafio não é tentativa
        auth = _cabecalho(sim, "DESCRIBE", url, "certa")
        r = _rtsp(s, f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 2\r\nAuthorization: {auth}\r\n\r\n")
        assert r.startswith("RTSP/1.0 200") and "H264/90000" in r
    assert sim.ler()["tentativasRtsp"] == 1
    enviado = recebidos[0].decode()
    assert enviado.startswith(f"DESCRIBE rtsp://127.0.0.1:{porta_mtx}/ch2s1 RTSP/1.0")
    assert "Authorization" not in enviado

    with socket.create_connection(("127.0.0.1", porta)) as s:
        auth = _cabecalho(sim, "DESCRIBE", url, "errada")
        r = _rtsp(s, f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 1\r\nAuthorization: {auth}\r\n\r\n")
        assert r.startswith("RTSP/1.0 401")
    assert sim.ler()["falhas"] == 1 and sim.ler()["tentativasRtsp"] == 2
    frente.servidor.shutdown()
