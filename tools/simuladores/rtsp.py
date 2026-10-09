"""A frente RTSP do DVR falso (laboratório do acesso ao DVR, ADR 0043 do NOC).

Fica na porta 554 do "DVR" e faz o que um gravador Intelbras/Dahua faz com quem pede vídeo:

- **Digest no DESCRIBE**, com o usuário e a senha do simulador. Pedido sem senha recebe o desafio
  (não conta); senha errada conta falha no MESMO contador da página HTTP, e passando de
  ``bloqueioAposFalhas`` o "DVR" bloqueia — é o que prova que o NOC tenta uma vez só.
- **Os caminhos de verdade**, traduzidos para o servidor de vídeo do laboratório (mediamtx, que não
  lê a consulta da URL): ``/cam/realmonitor?channel=N&subtype=S`` → ``/chNsS``,
  ``/cam/playback?channel=N&…`` → ``/pbN``, e os da Hikvision (``/Streaming/Channels/N01|N02``,
  ``/Streaming/tracks/N01``). Caminho desconhecido é 404, como no gravador.
- Depois do DESCRIBE aceito, tudo (SETUP, PLAY, o vídeo intercalado) passa adiante sem mexer.

Só a biblioteca padrão. Só para laboratório.
"""

from __future__ import annotations

import re
import socket
import socketserver
import threading
from typing import Any

_FIM = b"\r\n\r\n"
_PUBLICO = "OPTIONS, DESCRIBE, SETUP, TEARDOWN, PLAY, PAUSE, GET_PARAMETER"


def traduzir(url: str) -> str | None:
    """O caminho do mediamtx para a URL pedida, ou ``None`` (o gravador responde 404)."""
    m = re.match(r"^rtsp://[^/]+(/[^?]*)(?:\?(.*))?$", url)
    if not m:
        return None
    caminho, consulta = m.group(1), m.group(2) or ""
    q = dict(p.split("=", 1) for p in consulta.split("&") if "=" in p)
    if caminho.rstrip("/") == "/cam/realmonitor" and q.get("channel", "").isdigit():
        return f"/ch{int(q['channel'])}s{1 if q.get('subtype') == '1' else 0}"
    if caminho.rstrip("/") == "/cam/playback" and q.get("channel", "").isdigit():
        return f"/pb{int(q['channel'])}"
    h = re.match(r"^/Streaming/Channels/(\d+)(0[12])/?$", caminho)
    if h:
        return f"/ch{int(h.group(1))}s{0 if h.group(2) == '01' else 1}"
    t = re.match(r"^/Streaming/tracks/(\d+)01/?$", caminho)
    if t:
        return f"/pb{int(t.group(1))}"
    # SETUP/PLAY depois do DESCRIBE vêm com o Content-Base do mediamtx: já traduzidos.
    if re.match(r"^/(ch\d+s\d|pb\d+)(/.*)?$", caminho):
        return caminho
    return None


class FrenteRtsp:
    def __init__(self, simulador: Any, host: str, porta: int, destino: tuple[str, int]) -> None:
        self.simulador = simulador
        self.destino = destino
        frente = self

        class _Manipulador(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                _Conexao(frente, self.request).atender()

        self.servidor = socketserver.ThreadingTCPServer((host, porta), _Manipulador, bind_and_activate=False)
        self.servidor.allow_reuse_address = True
        self.servidor.daemon_threads = True
        self.servidor.server_bind()
        self.servidor.server_activate()

    def servir(self) -> None:
        self.servidor.serve_forever()


class _Conexao:
    def __init__(self, frente: FrenteRtsp, cliente: socket.socket) -> None:
        self.frente = frente
        self.cliente = cliente
        self.acima: socket.socket | None = None
        self.autenticado = False

    def responder(self, cseq: str, status: str, extra: str = "") -> None:
        self.cliente.sendall(f"RTSP/1.0 {status}\r\nCSeq: {cseq}\r\n{extra}\r\n".encode("latin-1"))

    def ligar_acima(self) -> socket.socket:
        if self.acima is None:
            self.acima = socket.create_connection(self.frente.destino, timeout=10)
            self.acima.settimeout(None)
            threading.Thread(target=self._bombear, daemon=True).start()
        return self.acima

    def _bombear(self) -> None:
        assert self.acima is not None
        try:
            while True:
                dados = self.acima.recv(65536)
                if not dados:
                    break
                self.cliente.sendall(dados)
        except OSError:
            pass
        finally:
            try:
                self.cliente.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def atender(self) -> None:
        buf = b""
        try:
            while True:
                dados = self.cliente.recv(65536)
                if not dados:
                    return
                buf += dados
                while buf:
                    if buf[:1] == b"$":
                        # Vídeo/RTCP intercalado do cliente: adiante como veio.
                        if len(buf) < 4:
                            break
                        n = 4 + int.from_bytes(buf[2:4], "big")
                        if len(buf) < n:
                            break
                        if self.acima:
                            self.acima.sendall(buf[:n])
                        buf = buf[n:]
                        continue
                    fim = buf.find(_FIM)
                    if fim < 0:
                        break
                    cabeca = buf[:fim].decode("latin-1")
                    linhas = cabeca.split("\r\n")
                    cab = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in linhas[1:]) if k}
                    corpo_n = int(cab.get("content-length", "0") or 0)
                    if len(buf) < fim + 4 + corpo_n:
                        break
                    corpo = buf[fim + 4 : fim + 4 + corpo_n]
                    buf = buf[fim + 4 + corpo_n :]
                    self.pedido(linhas, cab, corpo)
        except OSError:
            return
        finally:
            for s in (self.acima, self.cliente):
                try:
                    if s:
                        s.close()
                except OSError:
                    pass

    def pedido(self, linhas: list[str], cab: dict[str, str], corpo: bytes) -> None:
        metodo, url, versao = (linhas[0].split(" ") + ["", "", ""])[:3]
        cseq = cab.get("cseq", "0")
        sim = self.frente.simulador
        sim.pedidos.append((f"RTSP {metodo}", url))
        if metodo == "OPTIONS" and not self.autenticado:
            self.responder(cseq, "200 OK", f"Public: {_PUBLICO}\r\n")
            return
        if not self.autenticado:
            if not sim.conferir_login(metodo, cab.get("authorization"), rtsp=True):
                self.responder(cseq, "401 Unauthorized", f"WWW-Authenticate: {sim.desafio()}\r\n")
                return
            self.autenticado = True
        caminho = traduzir(url)
        if caminho is None:
            self.responder(cseq, "404 Not Found")
            return
        host, porta = self.frente.destino
        nova = f"rtsp://{host}:{porta}{caminho}"
        cabecalhos = [l for l in linhas[1:] if not l.lower().startswith("authorization:")]
        mensagem = "\r\n".join([f"{metodo} {nova} {versao}", *cabecalhos]).encode("latin-1") + _FIM + corpo
        try:
            self.ligar_acima().sendall(mensagem)
        except OSError:
            self.responder(cseq, "503 Service Unavailable")
