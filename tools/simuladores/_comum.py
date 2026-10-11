"""O que os três simuladores de gravador têm em comum.

- Servidor HTTP da biblioteca padrão (``http.server``), uma thread por pedido.
- **Autenticação digest** (RFC 2617, MD5, ``qop=auth``) escrita à mão — é o caminho
  que o adaptador real percorre (``httpx.DigestAuth``) contra Intelbras e Hikvision.
- **Estado controlável ao vivo**, sem autenticação, em ``/_sim/estado``:
  ``GET`` mostra, ``POST`` com JSON mistura no estado atual, ``POST /_sim/reiniciar``
  volta ao cenário inicial. É por aí que o dono (e os testes) mudam o cenário.
- **Registro de todo pedido** (método e caminho) em ``pedidos`` — os testes provam
  por ele que o agente só lê.

Só para laboratório: não use estes simuladores em rede de cliente.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

USUARIO_PADRAO = "admin"
SENHA_PADRAO = "simulador123"

ESTADO_INICIAL: dict[str, Any] = {
    # Quantos canais o gravador tem.
    "canais": 4,
    # Números (a partir de 1) dos canais sem vídeo agora.
    "semVideo": [],
    # Números dos canais que NÃO estão gravando.
    "naoGravando": [],
    # Um item por disco. estado: ok | defeito | cheio | ausente.
    "discos": [{"estado": "ok", "totalGb": 2000, "livreGb": 800}],
    # Quantos segundos o relógio do gravador está adiantado (negativo = atrasado).
    "desvioS": 0,
    # normal | vazia (HTTP 200 sem corpo) | malformada (HTTP 200 com lixo) — na leitura principal.
    "resposta": "normal",
}


def _md5(texto: str) -> str:
    return hashlib.md5(texto.encode("utf-8")).hexdigest()  # noqa: S324 - é o algoritmo do digest


def _campos_do_digest(cabecalho: str) -> dict[str, str]:
    campos: dict[str, str] = {}
    corpo = cabecalho[len("Digest ") :]
    for parte in _separar(corpo):
        if "=" not in parte:
            continue
        chave, valor = parte.split("=", 1)
        campos[chave.strip()] = valor.strip().strip('"')
    return campos


def _separar(texto: str) -> list[str]:
    """Separa por vírgula, respeitando aspas (o ``uri`` pode ter vírgula)."""
    partes, atual, aspas = [], "", False
    for ch in texto:
        if ch == '"':
            aspas = not aspas
        if ch == "," and not aspas:
            partes.append(atual)
            atual = ""
        else:
            atual += ch
    if atual:
        partes.append(atual)
    return partes


class Simulador:
    """O estado de um gravador simulado. ``manipulador`` responde os pedidos do fabricante."""

    fabricante = "gravador"
    realm = "Simulador"

    def __init__(self, usuario: str = USUARIO_PADRAO, senha: str = SENHA_PADRAO) -> None:
        self.usuario = usuario
        self.senha = senha
        self._trava = threading.Lock()
        self.estado: dict[str, Any] = copy.deepcopy(ESTADO_INICIAL)
        self.pedidos: list[tuple[str, str]] = []
        self._nonces: set[str] = set()

    # --- estado -------------------------------------------------------------------------
    def reiniciar(self) -> None:
        with self._trava:
            self.estado = copy.deepcopy(ESTADO_INICIAL)
            self.pedidos.clear()

    def mudar(self, parcial: dict[str, Any]) -> dict[str, Any]:
        with self._trava:
            self.estado.update(parcial)
            return copy.deepcopy(self.estado)

    def ler(self) -> dict[str, Any]:
        with self._trava:
            return copy.deepcopy(self.estado)

    def hora_local(self) -> datetime:
        """A hora local desta máquina mais o desvio do cenário (o gravador está na mesma loja)."""
        return datetime.now().astimezone() + timedelta(seconds=int(self.ler().get("desvioS") or 0))

    # --- digest -------------------------------------------------------------------------
    def desafio(self) -> str:
        nonce = secrets.token_hex(16)
        with self._trava:
            self._nonces.add(nonce)
        opaco = secrets.token_hex(8)
        return f'Digest realm="{self.realm}", qop="auth", nonce="{nonce}", opaque="{opaco}", algorithm=MD5'

    def digest_confere(self, metodo: str, cabecalho: str | None) -> bool:
        if not cabecalho or not cabecalho.startswith("Digest "):
            return False
        c = _campos_do_digest(cabecalho)
        if c.get("username") != self.usuario or c.get("nonce") not in self._nonces:
            return False
        ha1 = _md5(f"{self.usuario}:{self.realm}:{self.senha}")
        ha2 = _md5(f"{metodo}:{c.get('uri', '')}")
        if c.get("qop"):
            esperado = _md5(f"{ha1}:{c['nonce']}:{c.get('nc', '')}:{c.get('cnonce', '')}:{c['qop']}:{ha2}")
        else:
            esperado = _md5(f"{ha1}:{c['nonce']}:{ha2}")
        return secrets.compare_digest(esperado, c.get("response", ""))

    # --- o que cada fabricante implementa -----------------------------------------------
    def responder(self, h: Manipulador, metodo: str) -> None:  # pragma: no cover - abstrato
        raise NotImplementedError


class Manipulador(BaseHTTPRequestHandler):
    simulador: Simulador
    protocol_version = "HTTP/1.1"

    def log_message(self, formato: str, *args: Any) -> None:
        # Sem barulho no console; o que interessa está em /_sim/estado.
        return

    def date_time_string(self, timestamp: float | None = None) -> str:
        # O cabeçalho Date é o relógio do "gravador": leva o desvio do cenário (o UniFi usa).
        from email.utils import format_datetime

        return format_datetime(self.simulador.hora_local(), usegmt=False)

    def corpo_json(self) -> Any:
        tamanho = int(self.headers.get("Content-Length") or 0)
        bruto = self.rfile.read(tamanho) if tamanho else b""
        try:
            return json.loads(bruto or b"{}")
        except ValueError:
            return None

    def enviar(
        self,
        status: int,
        corpo: bytes | str = b"",
        tipo: str = "text/plain",
        cabecalhos: dict[str, str] | None = None,
    ) -> None:
        dados = corpo.encode("utf-8") if isinstance(corpo, str) else corpo
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        for k, v in (cabecalhos or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if dados and self.command != "HEAD":
            self.wfile.write(dados)

    def pedir_digest(self) -> None:
        self.enviar(401, "Unauthorized", cabecalhos={"WWW-Authenticate": self.simulador.desafio()})

    def _controle(self, metodo: str) -> bool:
        if not self.path.startswith("/_sim/"):
            return False
        sim = self.simulador
        if self.path == "/_sim/estado" and metodo == "GET":
            self.enviar(200, json.dumps({**sim.ler(), "pedidos": sim.pedidos[-50:]}), "application/json")
        elif self.path == "/_sim/estado" and metodo == "POST":
            parcial = self.corpo_json()
            if not isinstance(parcial, dict):
                self.enviar(400, "mande um objeto JSON")
            else:
                self.enviar(200, json.dumps(sim.mudar(parcial)), "application/json")
        elif self.path == "/_sim/reiniciar" and metodo == "POST":
            sim.reiniciar()
            self.enviar(200, json.dumps(sim.ler()), "application/json")
        else:
            self.enviar(404, "rota de controle desconhecida")
        return True

    def _tratar(self, metodo: str) -> None:
        if self._controle(metodo):
            return
        self.simulador.pedidos.append((metodo, self.path))
        self.simulador.responder(self, metodo)

    def do_GET(self) -> None:
        self._tratar("GET")

    def do_POST(self) -> None:
        self._tratar("POST")

    def do_PUT(self) -> None:
        self._tratar("PUT")

    def do_DELETE(self) -> None:
        self._tratar("DELETE")


def montar_servidor(simulador: Simulador, porta: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    classe = type(f"Manipulador{simulador.fabricante.title()}", (Manipulador,), {"simulador": simulador})
    servidor = ThreadingHTTPServer((host, porta), classe)
    servidor.daemon_threads = True
    return servidor


def rodar(fabrica: Callable[[str, str], Simulador], porta_padrao: int, nome: str) -> None:
    import argparse

    ap = argparse.ArgumentParser(description=f"Simulador de gravador {nome} (só laboratório).")
    ap.add_argument("--porta", type=int, default=porta_padrao)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--usuario", default=USUARIO_PADRAO)
    ap.add_argument("--senha", default=SENHA_PADRAO)
    args = ap.parse_args()
    sim = fabrica(args.usuario, args.senha)
    servidor = montar_servidor(sim, args.porta, args.host)
    print(f"Simulador {nome} no ar em http://{args.host}:{args.porta}")
    print(f"  usuário: {args.usuario}")
    print(f"  senha:   {args.senha}")
    print(f"  cenário: http://{args.host}:{args.porta}/_sim/estado  (GET mostra, POST muda)")
    print("  Ctrl+C para parar.", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
