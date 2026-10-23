"""Quem criou o ambiente, a partir do pedido HTTP (2.19.0, relatório de criação do NOC).

Duas origens possíveis para um pedido que chega às rotas com ``require_admin``:

- **túnel do NOC** — o agente acrescenta ``CABECALHO_DO_TUNEL`` (o id da sessão) a todo
  pedido HTTP e WebSocket que leva pelo túnel (``tunel.cabecalhos_do_pedido``). Vale só se o
  valor for o id de uma sessão **aberta neste processo** e o pedido vier desta máquina (a
  mesma regra do login, ``api/auth.py``). Aí o autor é o operador do NOC dono da sessão;
- **local** — qualquer outro caso, inclusive cabeçalho forjado ou de sessão já fechada:
  o autor é o usuário logado no middleware.
"""

from __future__ import annotations

from fastapi import Request

from middleware_monitor.api import auth as auth_api
from middleware_monitor.core.models import User
from middleware_monitor.domain.noc import tunel

__all__ = ["autoria_do_pedido", "operador_do_tunel"]


def operador_do_tunel(request: Request) -> str | None:
    """O operador do NOC, se o pedido veio por uma sessão de túnel viva; senão ``None``."""
    sessao = request.headers.get(tunel.CABECALHO_DO_TUNEL, "").strip()[:64]
    if not sessao:
        return None
    ip = (request.client.host if request.client else "") or ""
    if not (ip.startswith("127.") or ip in auth_api._ips_desta_maquina()):
        return None
    return tunel.operador_da_sessao(sessao)


def autoria_do_pedido(request: Request, user: User | None) -> tuple[str | None, str]:
    """``(criado_por, criado_via)`` para gravar no ambiente criado por este pedido.

    ``criado_via`` é ``"tunel"`` ou ``"local"``; quem cria por outro caminho (a tela de
    dispositivos) troca o ``via`` e mantém o autor."""
    operador = operador_do_tunel(request)
    if operador:
        return operador, "tunel"
    if user is None:
        user = getattr(request.state, "user", None)
    return (user.username if user is not None else None), "local"
