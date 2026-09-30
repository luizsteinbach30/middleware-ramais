"""Sondas de porta TCP e de página HTTP — a disponibilidade de equipamento (AGENTE-NOC item 19).

Ficam ao lado da ``PingProbe``, com a mesma regra: **só leitura**. A sonda TCP
abre a conexão e fecha, sem mandar um byte; a HTTP faz um ``GET`` sem corpo,
sem seguir redirecionamento e sem proxy do sistema (``trust_env=False``: o
equipamento está na rede da loja, e um proxy corporativo mentiria sobre ele).

Sem dependência nova: ``asyncio`` da biblioteca padrão e o ``httpx`` que o
projeto já usa. O ``.exe`` e o ``.run`` levam wheels cp311 exatas.

**Resposta vazia não é sucesso.** O status tem de ser o esperado, e um 2xx
(menos o 204, que por definição não tem corpo) sem nenhum byte de corpo é
falha: é o servidor web que subiu sem a aplicação atrás.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from typing import Any

import httpx

from middleware_monitor.integrations.network.base import ResultadoDaSonda

# Quanto do corpo basta para provar que a resposta tem forma. Não guardamos nada.
_BYTES_PARA_PROVAR_CORPO = 1


def _ms(inicio: float) -> int:
    return round((time.perf_counter() - inicio) * 1000)


def _segundos(timeout_s: float) -> str:
    return f"{timeout_s:g} s"


class SondaTcp:
    async def conectar(self, host: str, porta: int, timeout_s: float) -> ResultadoDaSonda:
        inicio = time.perf_counter()
        try:
            _leitor, escritor = await asyncio.wait_for(
                asyncio.open_connection(host, porta), timeout=timeout_s
            )
        except TimeoutError:
            return ResultadoDaSonda(ok=False, erro=f"a porta {porta} não respondeu em {_segundos(timeout_s)}")
        except ConnectionRefusedError:
            return ResultadoDaSonda(ok=False, erro=f"porta {porta} fechada (conexão recusada)")
        except socket.gaierror:
            return ResultadoDaSonda(ok=False, erro=f"o nome {host!r} não resolve")
        except OSError as exc:
            return ResultadoDaSonda(ok=False, erro=f"porta {porta}: {exc.strerror or exc}")
        valor = _ms(inicio)
        escritor.close()
        with contextlib.suppress(OSError, TimeoutError):
            await asyncio.wait_for(escritor.wait_closed(), timeout=1.0)
        return ResultadoDaSonda(ok=True, valor=valor)


def montar_url(host: str, parametros: dict[str, Any]) -> str:
    endereco = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{parametros['esquema']}://{endereco}:{parametros['porta']}{parametros['caminho']}"


class SondaHttp:
    async def pedir(  # noqa: PLR0911 - uma saída por forma de falha
        self, parametros: dict[str, Any], host: str, timeout_s: float
    ) -> ResultadoDaSonda:
        esperado = int(parametros["statusEsperado"])
        url = montar_url(host, parametros)
        inicio = time.perf_counter()
        try:
            async with (
                httpx.AsyncClient(
                    verify=bool(parametros.get("verificarCertificado", False)),
                    timeout=timeout_s,
                    follow_redirects=False,
                    trust_env=False,
                ) as cliente,
                cliente.stream("GET", url) as resposta,
            ):
                valor = _ms(inicio)
                corpo = b""
                if resposta.status_code == esperado and _precisa_de_corpo(esperado):
                    async for pedaco in resposta.aiter_bytes():
                        corpo += pedaco
                        if len(corpo) >= _BYTES_PARA_PROVAR_CORPO:
                            break
        except httpx.TimeoutException:
            return ResultadoDaSonda(ok=False, erro=f"a página não respondeu em {_segundos(timeout_s)}")
        except httpx.ConnectError as exc:
            return ResultadoDaSonda(ok=False, erro=f"não conectou: {_texto(exc)}")
        except httpx.RemoteProtocolError as exc:
            return ResultadoDaSonda(ok=False, erro=f"resposta fora do protocolo HTTP: {_texto(exc)}")
        except httpx.HTTPError as exc:
            return ResultadoDaSonda(ok=False, erro=f"{type(exc).__name__}: {_texto(exc)}")
        if resposta.status_code != esperado:
            return ResultadoDaSonda(ok=False, erro=f"HTTP {resposta.status_code}, esperado {esperado}")
        if _precisa_de_corpo(esperado) and not corpo:
            return ResultadoDaSonda(ok=False, erro=f"HTTP {esperado} com a resposta vazia")
        return ResultadoDaSonda(ok=True, valor=valor)


def _precisa_de_corpo(status: int) -> bool:
    return 200 <= status < 300 and status != 204


def _texto(exc: BaseException) -> str:
    return str(exc).strip() or type(exc).__name__
