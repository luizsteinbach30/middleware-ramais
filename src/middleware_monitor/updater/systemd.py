"""Pedido de atualização para a unidade systemd (instalação Linux pelo ``.run``).

O serviço roda como ``mmonitor`` com ``/opt`` somente-leitura e não pode se
atualizar. Ele deixa um arquivo em ``APP_DATA_DIR/update.request``; a unidade
``middleware-monitor-update.path`` aciona ``middleware-monitor-update`` como
root, que apaga o arquivo, baixa a release, confere o SHA256, troca o runtime,
migra o banco e reinicia o serviço.
"""

from __future__ import annotations

from pathlib import Path

from middleware_monitor.core.logging import get_logger
from middleware_monitor.settings import get_settings

log = get_logger("updater.systemd")

REQUEST_FILE = "update.request"


def request_path() -> Path:
    return get_settings().data_dir / REQUEST_FILE


def request_update(version: str) -> Path:
    """Grava o pedido de forma atômica: a unidade .path só vê o arquivo pronto."""
    path = request_path()
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(f"{version}\n", encoding="utf-8")
    tmp.replace(path)
    log.info("update_requested_via_systemd", version=version, path=str(path))
    return path
