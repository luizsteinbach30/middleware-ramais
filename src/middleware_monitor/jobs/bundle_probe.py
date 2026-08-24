"""Job: confere se os arquivos do executável ainda estão no disco.

Existe por causa de um caso de campo que ficou sem causa: um cliente rodando o
`.exe` recebeu `TemplateNotFound` para um template que **estava** dentro do
bundle. A hipótese principal é o antivírus (ou a limpeza de `%TEMP%`) esvaziar
o diretório de extração com o app no ar, mas faltava evidência — o erro só
aparecia quando alguém abria a tela, muito depois do fato.

Esta sonda fecha essa lacuna: de 15 em 15 minutos verifica os arquivos-canário
e, no minuto em que sumirem, grava um ERROR com a hora e o caminho. Só roda no
executável; do código-fonte não há diretório de extração para vigiar.
"""

from __future__ import annotations

from middleware_monitor.core import resources
from middleware_monitor.core.logging import get_logger

log = get_logger("bundle")

PROBE_JOB_ID = "bundle_probe"
PROBE_SECONDS = 900


async def run_bundle_probe() -> None:
    sumidos = resources.verificar_integridade()
    if not sumidos:
        log.debug("bundle_ok")
