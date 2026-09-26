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
    de repor a dívida. Uma rajada de até ``capacidade`` (1 s de banda) sai sem espera. ``kbps`` 0 = livre.
    """

    def __init__(self, kbps: int, *, relogio: object = time.monotonic) -> None:
        self.taxa = kbps * 1000 / 8  # bytes por segundo
        # Rajada de 1 s de banda: a página abre de uma vez; a média continua no limite.
        self.capacidade = max(64 * 1024, self.taxa)
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


# Só atributo de link do HTML. Valor de campo (``value=``), texto, JS e JSON ficam como estão:
# a 2.15.0/2.15.1 reescreviam toda URL de endereço interno e corrompiam a configuração que o
# telefone mostra (o servidor de provisionamento aparecia como /__tunel/ir?u=…, e salvar a tela
# gravaria isso no aparelho — reproduzido em 26/09).
_ATRIBUTO_DE_LINK = re.compile(
    rb"""(?P<antes>\b(?:href|src|action)\s*=\s*)(?P<aspa>["'])(?P<url>https?://[^"'\s<>]+)(?P=aspa)""",
    re.IGNORECASE,
)


def reescrever_atributos_v2(corpo: bytes, host_da_sessao: str) -> bytes:
    """Link do HTML para OUTRO endereço de dentro vira ``/__tunel/ir?u=<url>`` (o NOC abre a
    origem dele). O endereço do próprio aparelho já virou caminho pela regra da v1."""

    def trocar(m: re.Match[bytes]) -> bytes:
        url = m.group("url").decode("latin1")
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            return m.group(0)
        if not host or host == host_da_sessao.lower() or not host_interno(host):
            return m.group(0)
        aspa = m.group("aspa")
        return m.group("antes") + aspa + f"{CAMINHO_IR}?u={quote(url, safe='')}".encode() + aspa

    return _ATRIBUTO_DE_LINK.sub(trocar, corpo)


def location_para_outro_host(valor: str, host_da_sessao: str) -> str | None:
    """``Location`` para OUTRO endereço de dentro: ``/__tunel/ir``. ``None`` = segue a regra da v1
    (o próprio aparelho em qualquer esquema/porta vira caminho e troca o destino da sessão)."""
    try:
        partes = urlsplit(valor)
    except ValueError:
        return None
    host = (partes.hostname or "").lower()
    if partes.scheme not in {"http", "https"} or not host or host == host_da_sessao.lower():
        return None
    return f"{CAMINHO_IR}?u={quote(valor, safe='')}" if host_interno(host) else None
