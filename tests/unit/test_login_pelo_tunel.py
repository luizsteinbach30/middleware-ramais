"""A interface do middleware aberta pelo túnel do NOC (ADR 0007, emenda 2.14.2).

Pelo túnel todo operador chega como esta própria máquina. O bloqueio de login conta senhas
erradas por origem; sem separar, as tentativas de um operador travariam os outros e quem
usa o painel aqui mesmo.
"""

from __future__ import annotations

from starlette.requests import Request

from middleware_monitor.api.auth import _origem_do_login


def _pedido(ip: str, marca: str | None = None) -> Request:
    cabecalhos = [(b"x-noc-tunel", marca.encode())] if marca else []
    return Request({"type": "http", "client": (ip, 5000), "headers": cabecalhos})


def test_cada_sessao_do_tunel_conta_separado() -> None:
    a = _origem_do_login(_pedido("127.0.0.1", "sessao-a"))
    b = _origem_do_login(_pedido("127.0.0.1", "sessao-b"))
    local = _origem_do_login(_pedido("127.0.0.1"))
    assert len({a, b, local}) == 3


def test_a_marca_so_vale_vinda_desta_maquina() -> None:
    # De outro IP a marca seria um jeito de escapar do bloqueio: é ignorada.
    assert _origem_do_login(_pedido("192.0.2.10", "qualquer")) == "192.0.2.10"


def test_marca_longa_e_cortada() -> None:
    assert _origem_do_login(_pedido("127.0.0.1", "x" * 500)) == "127.0.0.1 tunel:" + "x" * 64
