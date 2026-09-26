"""Instalar uma release — o mesmo caminho para o botão local e para o pedido do NOC.

Antes daqui a instalação morava dentro da rota ``POST /api/system/update``. A
atualização automática (ADR 0008) precisa do mesmo passo a passo, e duas cópias
seriam dois lugares onde o Windows e o Linux divergem em silêncio.

Três modos, decididos pela instalação e não pelo pedido:

- **standalone** (``.exe`` do PyInstaller, Windows): baixa, confere o SHA256 e
  deixa o ajudante trocando o executável depois que este processo sair. Quem
  chama precisa encerrar o processo (``desktop.request_shutdown``).
- **systemd** (``.run`` no Linux): o serviço não pode se atualizar; grava o
  pedido com a versão exata, e a unidade ``middleware-monitor-update.path``
  instala como root.
- **legacy** (tarball com supervisor próprio): ``installer.install_release``,
  que já tem verificação de saúde e volta.
"""

from __future__ import annotations

import sys
import threading
from typing import Any

from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.tasks import spawn
from middleware_monitor.settings import get_settings
from middleware_monitor.updater.client import Release

log = get_logger("updater.instalar")


class FalhaNaInstalacao(RuntimeError):
    """A instalação não começou. A versão atual continua rodando."""


def modo() -> str:
    if getattr(sys, "frozen", False):
        return "standalone"
    if get_settings().resolved_update_mode() == "systemd":
        return "systemd"
    return "legacy"


def instalar(release: Release, *, encerrar_em_s: float = 1.0) -> dict[str, Any]:
    """Começa a instalação de ``release``. Devolve o que a tela e o NOC mostram.

    No Windows, este processo é encerrado ``encerrar_em_s`` segundos depois (dá
    tempo de a resposta HTTP sair). Levanta ``FalhaNaInstalacao`` quando nada foi
    trocado.
    """
    alvo = str(release.version)
    m = modo()
    if m == "standalone":
        from middleware_monitor.desktop import get_data_dir, request_shutdown
        from middleware_monitor.updater.standalone import (
            UpdateError,
            apply_standalone_update,
            find_exe_asset,
        )

        asset = find_exe_asset(release.assets)
        if asset is None:
            raise FalhaNaInstalacao(f"a release {alvo} não tem o .exe")
        sha = release.sha256sums
        sha_url = (sha.api_url or sha.download_url) if sha else None
        try:
            apply_standalone_update(
                asset_url=asset["url"],
                asset_name=asset["name"],
                data_dir=get_data_dir(),
                sha_url=sha_url,
                token=get_settings().effective_update_token,
                versao_esperada=alvo,
            )
        except (UpdateError, OSError) as exc:
            log.error("standalone_update_failed", error=str(exc))
            raise FalhaNaInstalacao(f"download ou conferência falhou: {exc}") from exc
        # O ajudante espera este PID morrer. A resposta HTTP sai antes.
        log.info("standalone_update_scheduled", version=alvo)
        threading.Timer(encerrar_em_s, request_shutdown).start()
        return {"ok": True, "mode": "standalone", "started_for": alvo, "shutdown_in_seconds": encerrar_em_s}

    if m == "systemd":
        from middleware_monitor.updater.systemd import request_update

        request_update(alvo)
        return {"ok": True, "mode": "systemd", "started_for": alvo}

    from middleware_monitor.updater.installer import install_release

    spawn(install_release(release))
    return {"ok": True, "mode": "legacy", "started_for": alvo}
