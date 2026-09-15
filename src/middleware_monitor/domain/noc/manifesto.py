"""O manifesto de capacidades — o que este agente conta ao NOC sobre si.

``docs/AGENTE-NOC.md``, item 4. O NOC **não infere nada** de versão nem de
modelo: o que não estiver aqui não existe para ele.

Duas regras:

- **``acoes`` é a lista de permissão do executor** (``executor.ACOES``), e nada
  mais. O que o adapter sabe fazer localmente vai por modelo (``acoesDoAdapter``),
  rotulado como o que é: ``set_ip`` existe para quem está na frente do aparelho,
  e não aparece em ``acoes``.
- **Nada volátil entra no corpo.** O sha256 do manifesto viaja em todo
  heartbeat, e o NOC só pede o corpo quando o hash muda: carimbo de hora aqui
  dentro faria o manifesto inteiro subir a cada minuto. ``ultimoBackupEm`` é a
  exceção que se paga: muda uma vez por backup, e é o que a tela de aprovação
  do NOC mostra antes de alguém autorizar uma escrita.
"""

from __future__ import annotations

import hashlib
import json
import platform
import socket
from datetime import UTC
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import ExtensionEnvironment, ExtensionLine
from middleware_monitor.domain.noc import executor
from middleware_monitor.domain.uscall import repository as uscall_repo
from middleware_monitor.domain.uscall import saude as uscall_saude
from middleware_monitor.version import __version__

VERSAO_DO_CONTRATO = 1

log = get_logger("noc.manifesto")


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
        "executorRemoto": True,
        "acoes": sorted(executor.ACOES),
        "ultimoBackupEm": ultimo_backup_em(),
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


def ultimo_backup_em() -> str | None:
    """O snapshot de banco mais recente (automático, manual ou o que antecede uma
    escrita remota), em UTC. Pacote portável e pré-restauração não contam."""
    from middleware_monitor.domain.backup import snapshot

    try:
        snaps = [b for b in snapshot.list_backups() if b.kind == "snapshot"]
    except OSError as exc:
        log.warning("noc_manifesto_sem_backups", motivo=str(exc))
        return None
    if not snaps:
        return None
    return snaps[0].modified_at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_do_manifesto(corpo: dict[str, Any]) -> str:
    """Hash do JSON canônico (chaves ordenadas, sem espaço), sem o próprio campo ``sha256``."""
    sem_hash = {k: v for k, v in corpo.items() if k != "sha256"}
    canonico = json.dumps(sem_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()
