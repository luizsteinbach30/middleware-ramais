"""Servidor de arquivos estáticos que sobrevive ao sumiço do disco.

Mesma classe de falha do `TemplateNotFound` relatado em campo: no PyInstaller
*onefile* os arquivos vivem numa pasta temporária que pode ser esvaziada com o
app no ar. Sem isto, a tela até responderia, mas sem CSS e sem JS — o que para
o operador é a mesma coisa que estar fora.

Só entra em ação quando o disco falha, e só tem conteúdo no executável
(:mod:`middleware_monitor.core.resources`).
"""

from __future__ import annotations

import mimetypes
from typing import Any

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from middleware_monitor.core import resources
from middleware_monitor.core.logging import get_logger

log = get_logger("static")


class ResilientStaticFiles(StaticFiles):
    """``StaticFiles`` com fallback para o cache em memória."""

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except (StarletteHTTPException, OSError) as exc:
            dados = resources.static_bytes(path)
            if dados is None:
                raise
            # WARNING e não DEBUG: servir da memória é sinal de que o ambiente
            # de execução foi alterado, e isso precisa aparecer no log de quem
            # for investigar depois.
            log.warning("static_servido_da_memoria", path=path, motivo=type(exc).__name__)
            tipo, _ = mimetypes.guess_type(path)
            return Response(
                dados,
                media_type=tipo or "application/octet-stream",
                headers={"Cache-Control": "no-cache"},
            )
