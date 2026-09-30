"""Roda uma verificação contra o equipamento — só leitura.

``icmp`` usa a ``PingProbe`` de sempre (a que pinga os ramais); nome de host é
resolvido aqui antes, porque a sonda de ping só aceita IP. ``tcp`` abre e fecha
a porta; ``http`` faz um ``GET`` sem corpo, com tempo limite curto; ``cftv`` lê o
gravador pelo adaptador do fabricante (``cftv.py``), com a credencial já aberta.
"""

from __future__ import annotations

import asyncio
import socket
import time
from datetime import UTC, datetime
from typing import Any

from middleware_monitor.domain.equipamentos import cftv
from middleware_monitor.integrations.network import make_http_probe, make_ping_probe, make_tcp_probe
from middleware_monitor.integrations.network.base import ResultadoDaSonda, is_valid_ip

TIMEOUT_TCP_S = 3.0
TIMEOUT_HTTP_S = 5.0
TIMEOUT_DNS_S = 3.0


async def _resolver(host: str) -> str | None:
    if is_valid_ip(host):
        return host
    laco = asyncio.get_running_loop()
    try:
        enderecos = await asyncio.wait_for(
            laco.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM),
            timeout=TIMEOUT_DNS_S,
        )
    except (OSError, TimeoutError):
        return None
    return str(enderecos[0][4][0]) if enderecos else None


async def executar(  # noqa: PLR0911 - uma saída por tipo e por falha
    tipo: str,
    host: str,
    parametros: dict[str, Any],
    *,
    timeout_ping_ms: int,
    verificacao: str = "",
    credencial: cftv.Credencial | None = None,
    respeitar_espera: bool = True,
) -> ResultadoDaSonda:
    try:
        if tipo == "cftv":
            inicio = time.perf_counter()
            lido = await cftv.ler(
                verificacao,
                host,
                parametros,
                credencial,
                datetime.now(UTC).replace(tzinfo=None),
                respeitar_espera=respeitar_espera,
            )
            ms = round((time.perf_counter() - inicio) * 1000)
            return ResultadoDaSonda(ok=lido.ok, valor=ms, erro=lido.erro, dados=lido.retrato)
        if tipo == "icmp":
            ip = await _resolver(host)
            if ip is None:
                return ResultadoDaSonda(ok=False, erro=f"o nome {host!r} não resolve")
            latencia = await make_ping_probe().ping(ip, timeout_ping_ms)
            if latencia is None:
                return ResultadoDaSonda(ok=False, erro=f"sem resposta ao ping em {timeout_ping_ms} ms")
            return ResultadoDaSonda(ok=True, valor=latencia)
        if tipo == "tcp":
            return await make_tcp_probe().conectar(host, int(parametros["porta"]), TIMEOUT_TCP_S)
        if tipo == "http":
            return await make_http_probe().pedir(parametros, host, TIMEOUT_HTTP_S)
    except Exception as exc:  # uma sonda quebrada é uma falha, nunca um job morto
        return ResultadoDaSonda(ok=False, erro=f"{type(exc).__name__}: {exc}"[:500])
    return ResultadoDaSonda(ok=False, erro=f"tipo de verificação desconhecido: {tipo!r}")
