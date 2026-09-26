"""O túnel de acesso web — ``docs/AGENTE-NOC.md``, item 14 (ADR 0007 deste repo).

Uma pessoa no NOC abre, no navegador dela, a interface web de um equipamento da
rede do cliente (telefone, PABX, roteador) ou de um servidor USCall cadastrado
aqui. **Quem abre a conexão é o middleware**: a tarefa ``abrir_acesso_web`` chega
pelo long-poll de sempre, e daqui sai um WebSocket para o canal do agente (mesmo
mTLS, mesmo Bearer). Por ele o NOC manda as requisições HTTP do navegador, e este
módulo faz a chamada real ao equipamento e devolve a resposta.

As regras que não se re-derivam lendo o código:

- **O destino é decidido aqui.** Endereço de LAN só vale se for IPv4 privado
  (10/8, 172.16/12, 192.168/16); loopback e o próprio middleware ficam de fora.
  USCall vem pelo nome do cadastro local — o NOC nunca manda endereço público
  solto, e o túnel não vira proxy para a internet.
- **Acesso completo, por decisão do dono (25/09).** O túnel deixa passar a página
  de rede do aparelho; é a exceção declarada ao "nenhuma tarefa mexe na rede". O
  que compensa é o registro: o NOC grava método e caminho de cada requisição, e
  aqui cada sessão fica no log com a pessoa que abriu.
- **Credencial nenhuma é injetada.** A pessoa digita a senha do equipamento na
  tela de login dele.
- **Sessão tem fim.** Sessenta minutos no máximo, contados aqui também — o NOC
  encerra antes, mas o middleware não depende disso para fechar.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from middleware_monitor.core.logging import get_logger
from middleware_monitor.version import __version__

log = get_logger("noc.tunel")

DURACAO_MAXIMA_S = 60 * 60
TIMEOUT_DO_EQUIPAMENTO_S = 30.0
# Conectar é rápido ou não é; esperar resposta pode ser longo (long-poll, página de status ao vivo).
TIMEOUTS = httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=120.0)
PEDACO = 64 * 1024
# Requisições em andamento por sessão. Pela Cloudflare o navegador fala HTTP/2 e dispara dezenas
# de uma vez; acima disto vira erro. As que passam esperam a vez na conexão com o equipamento.
MAXIMO_DE_FLUXOS = 64
# Conexões simultâneas com o equipamento: as de um navegador (6). Servidor web de telefone engasga
# com mais — era isto que fazia a página "começar bem e travar" (25/09).
CONEXOES_COM_O_EQUIPAMENTO = 6
# Resposta de texto que passa por reescrita de links precisa caber inteira na memória.
TEXTO_MAXIMO_PARA_REESCREVER = 8 * 1024 * 1024
# O frame JSON leva o pedaço em base64 (+33 %) e os cabeçalhos.
TAMANHO_MAXIMO_DO_FRAME = 1024 * 1024
RECONEXOES = (1, 2, 5, 10, 30)
# Formulário e upload de firmware cabem; mais que isso não é interface web.
CORPO_MAXIMO = 128 * 1024 * 1024

_REDES_DA_LAN = tuple(ipaddress.ip_network(r) for r in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_SESSAO = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NOME_USCALL = re.compile(r"^.{1,64}$", re.S)
# Cabeçalhos de salto: valem só entre dois pontos, nunca atravessam.
_SALTO = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)
_RESPOSTA_DESCARTADA = frozenset({"strict-transport-security", "alt-svc"})


class DestinoRecusado(ValueError):
    """O destino não passa nas regras daqui. A mensagem vai inteira para o NOC."""


@dataclass(frozen=True)
class Destino:
    esquema: str
    host: str
    porta: int
    rotulo: str

    @property
    def base(self) -> str:
        padrao = 443 if self.esquema == "https" else 80
        hospedeiro = self.host if self.porta == padrao else f"{self.host}:{self.porta}"
        return f"{self.esquema}://{hospedeiro}"

    @property
    def cabecalho_host(self) -> str:
        return urlsplit(self.base).netloc


# --- Destino ---------------------------------------------------------------------------------


def _ips_locais() -> set[str]:
    ips: set[str] = set()
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(str(info[4][0]))
    return ips


def destino_da_lan(
    destino: Any,
    porta: Any,
    esquema: Any,
    *,
    ips_locais: set[str] | None = None,
    porta_local: int | None = None,
) -> Destino:
    if esquema not in {"http", "https"}:
        raise DestinoRecusado("esquema: use http ou https.")
    if not (isinstance(porta, int) and not isinstance(porta, bool) and 1 <= porta <= 65535):
        raise DestinoRecusado("porta: use um inteiro entre 1 e 65535.")
    try:
        ip = ipaddress.ip_address(str(destino).strip())
    except ValueError as exc:
        raise DestinoRecusado("destino: informe um IPv4 da rede local (ex.: 192.168.0.20).") from exc
    if ip.version != 4 or not any(ip in rede for rede in _REDES_DA_LAN):
        raise DestinoRecusado(
            f"destino {ip}: o túnel só abre endereço da rede local (10.x, 172.16 a 172.31, 192.168.x)."
        )
    if (
        porta_local is not None
        and porta == porta_local
        and str(ip) in (_ips_locais() if ips_locais is None else ips_locais)
    ):
        raise DestinoRecusado("destino: é a própria interface deste middleware.")
    return Destino(esquema, str(ip), porta, f"{esquema}://{ip}:{porta}")


def destino_uscall(nome: Any, servidores: list[tuple[str, str]]) -> Destino:
    """``servidores`` = ``(nome, host)`` dos USCall habilitados aqui. O host segue a
    mesma regra do cliente USCall: sem esquema, sempre https."""
    if not (isinstance(nome, str) and _NOME_USCALL.match(nome)):
        raise DestinoRecusado("uscall: informe o nome do servidor como está cadastrado no middleware.")
    for cadastrado, host in servidores:
        if cadastrado == nome:
            limpo = host.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
            partes = urlsplit(f"https://{limpo}")
            if not partes.hostname:
                raise DestinoRecusado(f"O USCall {nome!r} está cadastrado sem endereço válido.")
            return Destino("https", partes.hostname, partes.port or 443, f"uscall:{nome}")
    raise DestinoRecusado(f"Não há USCall habilitado com o nome {nome!r} neste middleware.")


# --- Cabeçalhos ------------------------------------------------------------------------------


def cabecalhos_do_pedido(pares: list[list[str]], destino: Destino) -> list[tuple[str, str]]:
    """O que vai ao equipamento. ``Origin``/``Referer`` passam a apontar para ele:
    há firmware que recusa POST cujo Referer é de outro host."""
    saida: list[tuple[str, str]] = []
    for k, v in pares:
        nome = str(k).lower()
        if nome in _SALTO:
            continue
        valor = str(v)
        if nome in {"origin", "referer"}:
            partes = urlsplit(valor)
            if partes.scheme and partes.netloc:
                alvo = urlsplit(destino.base)
                valor = urlunsplit((alvo.scheme, alvo.netloc, partes.path, partes.query, partes.fragment))
                if nome == "origin":
                    valor = destino.base
        saida.append((str(k), valor))
    saida.append(("Host", destino.cabecalho_host))
    return saida


_DOMINIO_DO_COOKIE = re.compile(r";\s*domain=[^;]*", re.IGNORECASE)


def cabecalhos_da_resposta(pares: list[tuple[str, str]], destino: Destino) -> list[list[str]]:
    """O que volta ao navegador. ``Location`` absoluto do equipamento vira caminho
    (o navegador está no host de acesso do NOC, não no IP do aparelho), e o cookie
    perde o ``Domain`` — com ele, o navegador descartaria o cookie de sessão."""
    saida: list[list[str]] = []
    for k, v in pares:
        nome = k.lower()
        if nome in _SALTO or nome in _RESPOSTA_DESCARTADA:
            continue
        valor = v
        if nome in {"location", "content-location"}:
            partes = urlsplit(v)
            # O próprio equipamento, em qualquer esquema ou porta: o http que redireciona para
            # https é o mesmo aparelho, e a sessão passa a falar https com ele (``para_onde``).
            if partes.netloc and (partes.hostname or "").lower() == destino.host.lower():
                valor = urlunsplit(("", "", partes.path or "/", partes.query, partes.fragment))
        elif nome == "set-cookie":
            valor = _DOMINIO_DO_COOKIE.sub("", v)
        saida.append([k, valor])
    return saida


def para_onde(location: str, destino: Destino) -> Destino | None:
    """O destino novo quando o equipamento manda para ele mesmo em outro esquema ou porta.

    Telefone que só aceita https responde ao http com ``Location: https://<ip>/``. Tornar o
    ``Location`` relativo sem trocar o destino faria o navegador pedir de novo pelo http — e o
    aparelho redirecionar de novo, para sempre. ``None`` quando nada muda.
    """
    partes = urlsplit(location)
    if (partes.hostname or "").lower() != destino.host.lower() or partes.scheme not in {"http", "https"}:
        return None
    try:
        porta = partes.port or (443 if partes.scheme == "https" else 80)
    except ValueError:
        return None
    if (partes.scheme, porta) == (destino.esquema, destino.porta):
        return None
    return Destino(partes.scheme, destino.host, porta, destino.rotulo)


_TIPOS_REESCRITOS = ("text/html", "text/css", "javascript", "application/json", "text/xml", "application/xml")


def reescreve(tipo: str) -> bool:
    """Resposta cujo corpo pode trazer link absoluto para o próprio equipamento. Fluxo contínuo
    (``text/event-stream``) nunca: juntar o corpo inteiro seria nunca responder."""
    t = tipo.lower()
    return "event-stream" not in t and any(x in t for x in _TIPOS_REESCRITOS)


def reescrever_links(corpo: bytes, destino: Destino) -> bytes:
    """``http://<equipamento>[:porta]/x`` vira ``/x`` (e ``http://<equipamento>`` vira ``/``).

    Sem isto, o link absoluto que o firmware (ou o USCall) escreve na página leva o navegador
    direto ao IP da loja — fora do túnel, e para um endereço que de fora não existe (25/09).
    Só a forma de URL é trocada: o IP solto num campo de configuração continua como está.
    """
    host = re.escape(destino.host.encode("ascii", "ignore"))
    porta = rb"(?::\d{1,5})?"
    esquema = rb"(?:https?:)?"
    # Barras escapadas de JSON/JS (``http:\/\/ip\/x``) contam também.
    barra = rb"(?:/|\\/)"
    inicio = esquema + barra + barra + host + porta
    corpo = re.sub(inicio + rb"(?=" + barra + rb"|[?#])", b"", corpo, flags=re.I)
    return re.sub(inicio + rb"(?=[\"'\s<>)]|$)", b"/", corpo, flags=re.I)


# --- A sessão --------------------------------------------------------------------------------


def _b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode("ascii")


@dataclass
class _Fluxo:
    fila: asyncio.Queue[bytes | None]
    tarefa: asyncio.Task[None] | None = None


class Sessao:
    """Um WebSocket de saída e os fluxos HTTP que ele carrega."""

    def __init__(
        self, sessao_id: str, destino: Destino, *, canal: str, credencial: str, operador: str
    ) -> None:
        self.id = sessao_id
        self.destino = destino
        self.canal = canal
        self.credencial = credencial
        self.operador = operador
        self.inicio = time.monotonic()
        self.fluxos: dict[int, _Fluxo] = {}
        self.requisicoes = 0
        self._ws: Any = None
        self._envio = asyncio.Lock()
        self._http: httpx.AsyncClient | None = None
        self.encerrada = False

    @property
    def url(self) -> str:
        base = self.canal.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{base}/agente/v1/tunel/{self.id}"

    def _restante(self) -> float:
        return DURACAO_MAXIMA_S - (time.monotonic() - self.inicio)

    async def _enviar(self, frame: dict[str, Any]) -> None:
        if self._ws is None:
            return
        async with self._envio:
            await self._ws.send(json.dumps(frame, separators=(",", ":")))

    async def rodar(self) -> None:
        """Conecta, atende e reconecta enquanto o NOC não disser que acabou."""
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        from middleware_monitor.domain.noc import certificado as cert

        self._http = httpx.AsyncClient(
            verify=False,  # noqa: S501 - equipamento de LAN tem certificado próprio; os adapters fazem igual
            follow_redirects=False,
            timeout=TIMEOUTS,
            trust_env=False,
            # Keep-alive curto: telefone fecha a conexão parada em poucos segundos, e reusar uma
            # conexão morta é uma requisição perdida.
            limits=httpx.Limits(
                max_connections=CONEXOES_COM_O_EQUIPAMENTO,
                max_keepalive_connections=CONEXOES_COM_O_EQUIPAMENTO,
                keepalive_expiry=5.0,
            ),
        )
        tentativa = 0
        try:
            while not self.encerrada and self._restante() > 0:
                try:
                    tls = cert.contexto_do_agente() if self.url.startswith("wss://") else None
                    async with connect(
                        self.url,
                        ssl=tls,
                        additional_headers={"Authorization": f"Bearer {self.credencial}"},
                        user_agent_header=f"MiddlewareMonitor/{__version__}",
                        max_size=TAMANHO_MAXIMO_DO_FRAME * 2,
                        open_timeout=15,
                        proxy=None,
                        compression=None,
                    ) as ws:
                        self._ws = ws
                        tentativa = 0
                        log.info("noc_tunel_conectado", sessao=self.id, destino=self.destino.rotulo)
                        await asyncio.wait_for(self._atender(ws), timeout=max(1.0, self._restante()))
                except InvalidStatus as exc:
                    status = exc.response.status_code
                    # 404/410: a sessão não existe mais no NOC; 401/403: canal recusado.
                    log.info("noc_tunel_recusado", sessao=self.id, status=status)
                    if status in {401, 403, 404, 410}:
                        break
                except (ConnectionClosed, OSError, TimeoutError) as exc:
                    log.info("noc_tunel_caiu", sessao=self.id, motivo=type(exc).__name__)
                finally:
                    self._ws = None
                    self._cancelar_fluxos()
                if self.encerrada or self._restante() <= 0 or tentativa >= len(RECONEXOES):
                    break
                await asyncio.sleep(RECONEXOES[tentativa])
                tentativa += 1
        finally:
            self.encerrada = True
            await self._http.aclose()
            log.info(
                "noc_tunel_encerrado",
                sessao=self.id,
                destino=self.destino.rotulo,
                operador=self.operador,
                requisicoes=self.requisicoes,
                duracao_s=int(time.monotonic() - self.inicio),
            )

    async def _atender(self, ws: Any) -> None:
        async for mensagem in ws:
            if isinstance(mensagem, bytes):
                continue
            try:
                frame = json.loads(mensagem)
            except ValueError:
                continue
            if not isinstance(frame, dict):
                continue
            await self.receber(frame)
            if self.encerrada:
                await ws.close()
                return

    async def receber(self, frame: dict[str, Any]) -> None:  # noqa: PLR0911 - um ramo por tipo de frame
        tipo, fluxo = frame.get("t"), frame.get("f")
        if tipo == "fechar":
            self.encerrada = True
            return
        if not (isinstance(fluxo, int) and not isinstance(fluxo, bool)):
            return
        if tipo == "req":
            if fluxo in self.fluxos:
                return
            if len(self.fluxos) >= MAXIMO_DE_FLUXOS:
                await self._enviar(
                    {"t": "erro", "f": fluxo, "mensagem": "Muitas requisições ao mesmo tempo nesta sessão."}
                )
                return
            novo = _Fluxo(asyncio.Queue())
            self.fluxos[fluxo] = novo
            novo.tarefa = asyncio.create_task(self._executar(fluxo, frame, novo))
            return
        f = self.fluxos.get(fluxo)
        if f is None:
            return
        if tipo == "req.corpo":
            try:
                f.fila.put_nowait(base64.b64decode(str(frame.get("dados") or ""), validate=True))
            except ValueError:
                if f.tarefa:
                    f.tarefa.cancel()
            return
        if tipo == "req.fim":
            f.fila.put_nowait(None)
            return
        if tipo == "cancelar" and f.tarefa:
            f.tarefa.cancel()

    async def _corpo(self, f: _Fluxo) -> bytes:
        """O corpo inteiro, até o ``req.fim``. Juntar em vez de repassar em pedaços é
        de propósito: repassar viraria ``Transfer-Encoding: chunked``, e servidor web
        de telefone costuma não aceitar."""
        partes: list[bytes] = []
        total = 0
        while True:
            pedaco = await f.fila.get()
            if pedaco is None:
                return b"".join(partes)
            total += len(pedaco)
            if total > CORPO_MAXIMO:
                raise ValueError(f"corpo acima de {CORPO_MAXIMO // (1024 * 1024)} MB")
            partes.append(pedaco)

    async def _mandar(
        self, metodo: str, caminho: str, pares: list[list[str]], corpo: bytes
    ) -> httpx.Response:
        """Manda ao equipamento. GET/HEAD que caem numa conexão que o aparelho já fechou vão de
        novo, uma vez, numa conexão nova — o navegador faz o mesmo."""
        assert self._http is not None
        cabecalhos = cabecalhos_do_pedido(pares, self.destino)
        for tentativa in (1, 2):
            pedido = self._http.build_request(
                metodo, f"{self.destino.base}{caminho}", headers=cabecalhos, content=corpo or None
            )
            try:
                return await self._http.send(pedido, stream=True)
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError):
                if tentativa == 2 or metodo not in {"GET", "HEAD", "OPTIONS"}:
                    raise
        raise AssertionError("inalcançável")

    async def _executar(self, fluxo: int, frame: dict[str, Any], f: _Fluxo) -> None:
        metodo = str(frame.get("metodo") or "GET").upper()
        caminho = str(frame.get("caminho") or "/")
        if not caminho.startswith("/"):
            caminho = "/" + caminho
        bruto = frame.get("cabecalhos")
        cabecalhos: list[Any] = bruto if isinstance(bruto, list) else []
        pares = [p for p in cabecalhos if isinstance(p, list) and len(p) == 2]
        # Só compressão que o httpx sabe abrir: a página reescrita precisa ser lida.
        pares = [p for p in pares if str(p[0]).lower() != "accept-encoding"]
        pares.append(["Accept-Encoding", "gzip, deflate"])
        self.requisicoes += 1
        try:
            corpo = await asyncio.wait_for(self._corpo(f), timeout=TIMEOUT_DO_EQUIPAMENTO_S * 4)
            resposta = await self._mandar(metodo, caminho, pares, corpo)
            try:
                await self._responder(fluxo, resposta)
            finally:
                await resposta.aclose()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self._erro(fluxo, "O NOC não terminou de mandar o corpo da requisição.")
        except httpx.TimeoutException:
            await self._erro(fluxo, "O equipamento não respondeu a tempo.")
        except httpx.ConnectError:
            await self._erro(
                fluxo, f"Não foi possível conectar em {self.destino.base} a partir do middleware."
            )
        except Exception as exc:  # o fluxo sempre ganha resposta
            await self._erro(fluxo, f"{type(exc).__name__}: {exc}"[:300])
        finally:
            self.fluxos.pop(fluxo, None)

    async def _responder(self, fluxo: int, resposta: httpx.Response) -> None:
        destino = self.destino
        local = resposta.headers.get("location")
        novo = para_onde(local, destino) if local and 300 <= resposta.status_code < 400 else None
        if novo is not None:
            log.info("noc_tunel_destino_mudou", sessao=self.id, de=destino.base, para=novo.base)
            self.destino = novo
        cabecalhos = cabecalhos_da_resposta(list(resposta.headers.multi_items()), destino)
        if reescreve(resposta.headers.get("content-type", "")) and resposta.status_code != 206:
            texto = b""
            async for pedaco in resposta.aiter_bytes():
                texto += pedaco
                if len(texto) > TEXTO_MAXIMO_PARA_REESCREVER:
                    raise ValueError("página grande demais para reescrever os links")
            texto = reescrever_links(texto, destino)
            # aiter_bytes já descomprimiu: o Content-Encoding do equipamento não vale mais.
            cabecalhos = [c for c in cabecalhos if c[0].lower() != "content-encoding"]
            await self._enviar(
                {"t": "resp", "f": fluxo, "status": resposta.status_code, "cabecalhos": cabecalhos}
            )
            for i in range(0, len(texto), PEDACO):
                await self._enviar({"t": "resp.corpo", "f": fluxo, "dados": _b64(texto[i : i + PEDACO])})
        else:
            await self._enviar(
                {"t": "resp", "f": fluxo, "status": resposta.status_code, "cabecalhos": cabecalhos}
            )
            # Cada pedaço sai assim que chega. Juntar até 64 KB prendia a página de status ao
            # vivo, que chega em pedacinhos e nunca completa o bloco (25/09).
            async for pedaco in resposta.aiter_raw():
                for i in range(0, len(pedaco), PEDACO):
                    await self._enviar({"t": "resp.corpo", "f": fluxo, "dados": _b64(pedaco[i : i + PEDACO])})
        await self._enviar({"t": "resp.fim", "f": fluxo})

    async def _erro(self, fluxo: int, mensagem: str) -> None:
        with contextlib.suppress(Exception):
            await self._enviar({"t": "erro", "f": fluxo, "mensagem": mensagem})

    def _cancelar_fluxos(self) -> None:
        for f in list(self.fluxos.values()):
            if f.tarefa and not f.tarefa.done():
                f.tarefa.cancel()
        self.fluxos.clear()


# --- Registro das sessões abertas --------------------------------------------------------------

_sessoes: dict[str, Sessao] = {}
_tarefas: set[asyncio.Task[None]] = set()


def abrir(sessao_id: str, destino: Destino, *, canal: str, credencial: str, operador: str) -> bool:
    """Liga a sessão em segundo plano. Devolve ``False`` se ela já estava aberta
    (a tarefa foi reentregue) — nada é aberto duas vezes."""
    if not _SESSAO.match(sessao_id):
        raise DestinoRecusado("sessao inválida.")
    atual = _sessoes.get(sessao_id)
    if atual is not None and not atual.encerrada:
        return False
    sessao = Sessao(sessao_id, destino, canal=canal, credencial=credencial, operador=operador)
    _sessoes[sessao_id] = sessao
    log.info("noc_tunel_aberto", sessao=sessao_id, destino=destino.rotulo, operador=operador)

    async def _rodar() -> None:
        try:
            await sessao.rodar()
        except Exception as exc:  # nada daqui derruba o laço de tarefas
            log.error("noc_tunel_quebrou", sessao=sessao_id, erro=f"{type(exc).__name__}: {exc}")
        finally:
            _sessoes.pop(sessao_id, None)

    tarefa = asyncio.get_running_loop().create_task(_rodar())
    _tarefas.add(tarefa)
    tarefa.add_done_callback(_tarefas.discard)
    return True


def abertas() -> list[dict[str, Any]]:
    return [
        {
            "sessao": s.id,
            "destino": s.destino.rotulo,
            "operador": s.operador,
            "requisicoes": s.requisicoes,
            "abertaHaS": int(time.monotonic() - s.inicio),
        }
        for s in _sessoes.values()
        if not s.encerrada
    ]
