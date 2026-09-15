"""O manifesto de capacidades — o que este agente conta ao NOC sobre si.

``docs/AGENTE-NOC.md``, item 4. O NOC **não infere nada** de versão nem de
modelo: o que não estiver aqui não existe para ele.

Duas regras:

- **``acoes`` só declara o que o agente executa A PEDIDO DO NOC.** Na v2.13.0
  isso é nada: o executor remoto nasce na Fase 2. Declarar ``normalize`` agora —
  porque o adapter sabe normalizar localmente — seria prometer ao NOC uma ação
  que ninguém aqui vai executar quando ele pedir. As capacidades locais vão por
  modelo (``acoesDoAdapter``), rotuladas como o que são.
- **Nada volátil entra no corpo.** O sha256 do manifesto viaja em todo
  heartbeat, e o NOC só pede o corpo quando o hash muda: carimbo de hora aqui
  dentro faria o manifesto inteiro subir a cada minuto.
"""

from __future__ import annotations

import hashlib
import json
import platform
import socket
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.models import ExtensionEnvironment, ExtensionLine
from middleware_monitor.domain.uscall import repository as uscall_repo
from middleware_monitor.domain.uscall import saude as uscall_saude
from middleware_monitor.version import __version__

VERSAO_DO_CONTRATO = 1


def nome_da_maquina() -> str:
    return (socket.gethostname() or "desconhecida")[:120]


def sistema() -> str:
    return platform.system().lower() or "desconhecido"


def _capacidades_do_modelo(modelo: str) -> list[str]:
    from middleware_monitor.domain.extension_configurator.actions import capabilities_for

    try:
        return capabilities_for(modelo)
    except Exception:
        # Modelo que nenhum adapter reconhece não derruba o manifesto inteiro.
        return []


def montar(db: DBSession) -> dict[str, Any]:
    # O modelo é o cadastrado no AMBIENTE — é a fonte da verdade do configurador,
    # não o que o aparelho respondeu na última aplicação.
    linhas = db.execute(
        select(ExtensionEnvironment.modelo_telefone, func.count(ExtensionLine.id))
        .outerjoin(ExtensionLine, ExtensionLine.environment_id == ExtensionEnvironment.id)
        .group_by(ExtensionEnvironment.modelo_telefone)
        .order_by(ExtensionEnvironment.modelo_telefone)
    ).all()

    servidores = uscall_repo.list_servers(db, enabled_only=True)

    corpo: dict[str, Any] = {
        "versaoDoContrato": VERSAO_DO_CONTRATO,
        "versao": __version__,
        "maquina": nome_da_maquina(),
        "sistema": sistema(),
        "executorRemoto": False,
        "acoes": [],
        "modelos": [
            {"modelo": modelo, "quantidade": int(qtd), "acoesDoAdapter": _capacidades_do_modelo(modelo)}
            for modelo, qtd in linhas
        ],
        "uscall": [
            {"nome": s.nome, "endereco": s.host, "alcancavel": uscall_saude.alcancavel(s.nome)}
            for s in servidores
        ],
    }
    corpo["sha256"] = sha256_do_manifesto(corpo)
    return corpo


def sha256_do_manifesto(corpo: dict[str, Any]) -> str:
    """Hash do JSON canônico (chaves ordenadas, sem espaço), sem o próprio campo ``sha256``."""
    sem_hash = {k: v for k, v in corpo.items() if k != "sha256"}
    canonico = json.dumps(sem_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()
