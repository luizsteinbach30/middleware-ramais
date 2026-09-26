"""O protocolo v2 do túnel (ADR 0009 deste repo; NOC ADR 0028; CONTRATO-DO-AGENTE §11.6).

Tudo aqui é puro — sem rede nem sessão — para que as regras se testem sozinhas:

- **Negociação:** o agente pede ``X-Tunel-Protocolo: 2`` no handshake do WebSocket; o NOC
  que entende devolve o mesmo cabeçalho e o limite de banda (``X-Tunel-Banda-Kbps``). NOC
  antigo não devolve nada, e a sessão segue na v1 (JSON com base64), como na 2.14.
- **Corpo em frame binário:** 1 byte de tipo + 4 bytes do fluxo + os dados. Sem o base64 da
  v1, que gastava 33 % a mais no link da loja.
- **Janela por fluxo:** o corpo de uma resposta só anda enquanto houver crédito; o NOC
  devolve crédito depois de entregar ao navegador. Um download grande não prende o menu
  aberto na outra aba — é o controle de fluxo por stream do HTTP/2 e do yamux.
- **Balde de banda por sessão:** o que sobe do agente para o NOC não passa de
  ``X-Tunel-Banda-Kbps`` (0 = sem limite). O túnel divide o link com a telefonia.
- **Origens:** o link absoluto para outro endereço interno vira ``/__tunel/ir?u=<url>``; é
  o NOC quem cria a origem (subdomínio próprio) e leva o navegador até ela.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import struct
import time
from urllib.parse import quote, urlsplit

PROTOCOLO = "2"
CABECALHO_PROTOCOLO = "X-Tunel-Protocolo"
CABECALHO_BANDA = "X-Tunel-Banda-Kbps"

# Tipos de frame binário.
REQ_CORPO = 0x01  # NOC -> agente
RESP_CORPO = 0x02  # agente -> NOC
WS_TEXTO = 0x03  # nos dois sentidos
WS_BINARIO = 0x04  # nos dois sentidos
_TIPOS = frozenset({REQ_CORPO, RESP_CORPO, WS_TEXTO, WS_BINARIO})
_CABECA = struct.Struct(">BI")

JANELA_INICIAL = 256 * 1024
CAMINHO_IR = "/__tunel/ir"


def empacotar(tipo: int, fluxo: int, dados: bytes) -> bytes:
    return _CABECA.pack(tipo, fluxo) + dados


def desempacotar(quadro: bytes) -> tuple[int, int, bytes] | None:
    """``(tipo, fluxo, dados)``, ou ``None`` para quadro fora do contrato."""
    if len(quadro) < _CABECA.size:
        return None
    tipo, fluxo = _CABECA.unpack_from(quadro)
    if tipo not in _TIPOS:
        return None
    return tipo, fluxo, quadro[_CABECA.size :]


def banda_do_cabecalho(valor: str | None) -> int:
    """kbit/s anunciados pelo NOC; qualquer coisa fora de forma vale 0 (sem limite)."""
    try:
        kbps = int(str(valor or "0").strip())
    except ValueError:
        return 0
    return kbps if 0 < kbps <= 10_000_000 else 0


class Balde:
    """Limite de banda por sessão (token bucket com dívida).

    Cada envio gasta os bytes na hora; se o saldo fica negativo, quem enviou espera o tempo
    de repor a dívida. Uma rajada de até ``capacidade`` sai sem espera. ``kbps`` 0 = livre.
    """

    def __init__(self, kbps: int, *, relogio: object = time.monotonic) -> None:
        self.taxa = kbps * 1000 / 8  # bytes por segundo
        self.capacidade = max(64 * 1024, self.taxa / 4)
        self.saldo = self.capacidade
        self._relogio = relogio
        self._ultimo = self._agora()
        self._trava = asyncio.Lock()

    def _agora(self) -> float:
        return float(self._relogio())  # type: ignore[operator]

    def espera_para(self, n: int) -> float:
        """Gasta ``n`` bytes e diz quantos segundos esperar (0 = pode mandar já)."""
        if self.taxa <= 0:
            return 0.0
        agora = self._agora()
        self.saldo = min(self.capacidade, self.saldo + (agora - self._ultimo) * self.taxa)
        self._ultimo = agora
        self.saldo -= n
        return 0.0 if self.saldo >= 0 else -self.saldo / self.taxa

    async def gastar(self, n: int) -> None:
        if self.taxa <= 0:
            return
        async with self._trava:
            espera = self.espera_para(n)
            if espera > 0:
                await asyncio.sleep(espera)


# --- Origens: o que é "de dentro" ------------------------------------------------------------

_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_SUFIXOS_INTERNOS = (".local", ".lan", ".internal", ".intranet", ".home.arpa", ".localdomain", ".corp")


def host_interno(host: str) -> bool:
    """Endereço que só existe do lado de dentro (e por isso tem de passar pelo túnel).

    IP privado, loopback, link-local e CGNAT; nome sem ponto (``pabx``) ou com sufixo de rede
    interna. Endereço público embutido na página (uma CDN) fica direto: o navegador do
    operador busca sozinho, sem gastar o link da loja.
    """
    h = host.strip("[]").lower().rstrip(".")
    if not h:
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return "." not in h or h.endswith(_SUFIXOS_INTERNOS)
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or (ip.version == 4 and ip in _CGNAT))


def _porta_padrao(esquema: str) -> int:
    return 443 if esquema == "https" else 80


def para_o_tunel(url: str, esquema: str, host: str, porta: int) -> str | None:
    """O que o navegador deve pedir no lugar de ``url`` absoluto, estando na origem
    ``esquema://host:porta``: caminho (a mesma origem), ``/__tunel/ir?u=`` (outra origem de
    dentro) ou ``None`` (fica como está — endereço público, ou forma que não é URL)."""
    completo = f"{esquema}:{url}" if url.startswith("//") else url
    try:
        partes = urlsplit(completo)
        porta_url = partes.port
    except ValueError:
        return None
    if partes.scheme not in {"http", "https"} or not partes.hostname:
        return None
    porta_url = porta_url or _porta_padrao(partes.scheme)
    caminho = (partes.path or "/") + (f"?{partes.query}" if partes.query else "")
    if partes.hostname.lower() == host.lower() and partes.scheme == esquema and porta_url == porta:
        return caminho + (f"#{partes.fragment}" if partes.fragment else "")
    if partes.hostname.lower() == host.lower() or host_interno(partes.hostname):
        return f"{CAMINHO_IR}?u={quote(completo, safe='')}"
    return None


# URL absoluta (ou relativa ao esquema) dentro de HTML/CSS/JS/JSON, com barras escapadas de JSON.
_URL = re.compile(
    rb"(?P<esquema>https?:)?(?P<barras>//|\\/\\/)(?P<host>\[[0-9a-fA-F:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)"
    rb"(?P<porta>:\d{1,5})?(?P<resto>(?:/|\\/)[^\s\"'<>)\\]*(?:\\/[^\s\"'<>)\\]*)*)?"
)


def reescrever_links_v2(corpo: bytes, esquema: str, host: str, porta: int) -> bytes:
    """Cada URL absoluta do corpo vai para onde o navegador a alcança pelo túnel
    (:func:`para_o_tunel`). Protocolo v2: é o NOC que resolve ``/__tunel/ir``."""

    def trocar(m: re.Match[bytes]) -> bytes:
        esquema_url = m.group("esquema")
        if esquema_url is None and m.start() > 0 and corpo[m.start() - 1 : m.start()] in {b"/", b"\\", b":"}:
            return m.group(0)  # pedaço de outra URL, não o começo de uma
        escapada = m.group("barras") == b"\\/\\/"
        bruto = m.group(0).decode("latin1")
        url = bruto.replace("\\/", "/")
        novo = para_o_tunel(url, esquema, host, porta)
        if novo is None:
            return m.group(0)
        if escapada:
            novo = novo.replace("/", "\\/")
        return novo.encode("latin1")

    return _URL.sub(trocar, corpo)


def location_v2(valor: str, esquema: str, host: str, porta: int) -> str:
    """``Location`` do equipamento: caminho na mesma origem, ``/__tunel/ir`` para outra de
    dentro — inclusive o próprio aparelho em outro esquema ou porta (o http que manda para
    o https ganha a sua origem, em vez de trocar o destino da sessão como na v1)."""
    if not urlsplit(valor).netloc:
        return valor
    return para_o_tunel(valor, esquema, host, porta) or valor
