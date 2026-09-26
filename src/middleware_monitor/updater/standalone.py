"""Standalone single-exe self-update (Windows).

This is the only update path that is actually wired up for the desktop
build delivered as ``MiddlewareMonitor-X.Y.Z.exe``. The legacy
``installer.py`` (tarball + NSSM / systemd swap) does not apply when the
whole application is a single PyInstaller executable.

Flow:

1. Find the ``MiddlewareMonitor-*.exe`` asset in the GitHub release.
2. Download it (authenticated — the releases repo is private) to
   ``%LOCALAPPDATA%/MiddlewareMonitor/tmp``.
3. Verify the SHA256 of the downloaded ``.exe`` against the release's
   ``SHA256SUMS`` asset. Mismatch aborts the update and deletes the file.
4. Write a small ``apply_update.bat`` helper that:
   - waits for the current PID to terminate,
   - keeps the running ``.exe`` as ``<nome>.exe.bak``,
   - moves the new ``.exe`` over the running one and re-launches it,
   - **confere a saúde** (ADR 0008): ``/api/system/healthz`` tem de responder com a
     versão nova em até 150 s; senão encerra a nova, devolve o ``.bak`` e sobe a
     antiga — sem isso, uma release que não abre deixava o cliente sem middleware
     até alguém ir lá,
   - grava o desfecho em ``update_result.txt`` (lido no boot seguinte e mandado
     ao NOC) e se apaga.
5. Spawn the helper detached (no console window) so it survives our exit.
6. Caller is expected to terminate the process so the helper can swap the
   binary (Windows refuses to overwrite a running ``.exe``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from middleware_monitor.core.logging import get_logger
from middleware_monitor.updater.checksums import verify_file
from middleware_monitor.updater.client import download_asset_sync

log = get_logger("updater")


class UpdateError(RuntimeError):
    pass


def find_exe_asset(assets: list[dict[str, Any]] | list[Any]) -> dict[str, Any] | None:
    """Return the first asset whose name looks like ``MiddlewareMonitor-X.Y.Z.exe``.

    Accepts either raw GitHub API dicts (``{"name": ..., "url": ...,
    "browser_download_url": ...}``) or ``ReleaseAsset`` objects from
    ``updater.client``. The returned ``url`` prefers the asset **API URL**
    (required for private repos)."""
    for a in assets:
        if isinstance(a, dict):
            name = a.get("name", "")
            url = a.get("url") or a.get("browser_download_url") or a.get("download_url", "")
        else:
            name = getattr(a, "name", "")
            url = getattr(a, "api_url", "") or getattr(a, "download_url", "")
        if not name:
            continue
        if name.startswith("MiddlewareMonitor") and name.endswith(".exe"):
            return {"name": name, "url": url}
    return None


def apply_standalone_update(
    *,
    asset_url: str,
    asset_name: str,
    data_dir: Path,
    current_exe: Path | None = None,
    sha_url: str | None = None,
    token: str | None = None,
    versao_esperada: str | None = None,
    porta: int | None = None,
) -> Path:
    """Download the new ``.exe`` and spawn the helper that will swap it in.

    Returns the path to the freshly-downloaded ``.exe`` (in tmp). After
    this call returns, the caller MUST terminate the current process —
    otherwise the helper batch will wait forever for the PID to die.
    """
    if not sys.platform.startswith("win"):
        raise UpdateError("standalone update is only supported on Windows")
    if not getattr(sys, "frozen", False):
        raise UpdateError("standalone update only runs from the PyInstaller .exe")
    if not sha_url:
        raise UpdateError("release missing SHA256SUMS (required for integrity check)")

    tmp_dir = data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    new_exe = tmp_dir / asset_name

    log.info("update_download_started", asset=asset_name, url=asset_url)
    try:
        download_asset_sync(asset_url, new_exe, token=token)
    except Exception as exc:
        raise UpdateError(f"download failed: {exc}") from exc
    log.info("update_download_done", path=str(new_exe), bytes=new_exe.stat().st_size)

    sums_path = tmp_dir / "SHA256SUMS"
    try:
        download_asset_sync(sha_url, sums_path, token=token)
        verify_file(new_exe, sums_path, target_name=asset_name)
    except Exception as exc:
        new_exe.unlink(missing_ok=True)
        sums_path.unlink(missing_ok=True)
        raise UpdateError(f"checksum verification failed: {exc}") from exc
    log.info("update_checksum_ok", asset=asset_name)

    current_exe = current_exe or Path(sys.executable).resolve()
    pid = os.getpid()
    helper = tmp_dir / "apply_update.bat"
    helper.write_text(
        script_do_ajudante(
            pid=pid,
            novo=new_exe,
            atual=current_exe,
            alvo=versao_esperada or "",
            porta=porta or _porta_local(),
            resultado=data_dir / RESULTADO,
        ),
        encoding="utf-8",
    )

    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    )
    subprocess.Popen(
        ["cmd.exe", "/c", str(helper)],
        creationflags=creationflags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log.info("update_helper_spawned", helper=str(helper), pid_to_wait=pid)
    return new_exe


RESULTADO = "update_result.txt"
ESPERA_DA_SAUDE_S = 150


def _porta_local() -> int:
    from middleware_monitor.settings import get_settings

    return int(get_settings().port)


def script_do_ajudante(*, pid: int, novo: Path, atual: Path, alvo: str, porta: int, resultado: Path) -> str:
    """O ``.bat`` que troca o executável depois que este processo sai.

    Sem ``alvo`` (versão desconhecida) a saúde aceita qualquer versão que
    responda: ainda prova que o executável novo abre. As mensagens não levam
    parênteses — dentro de bloco ``if (...)`` do cmd eles fecham o bloco.
    """
    bak = atual.with_name(atual.name + ".bak")
    confere = (
        f"powershell -NoProfile -NonInteractive -Command \"try{{(Invoke-RestMethod -UseBasicParsing "
        f"-TimeoutSec 4 'http://127.0.0.1:{porta}/api/system/healthz').version}}catch{{}}\""
    )
    condicao = '"%V%"=="%ALVO%"' if alvo else 'not "%V%"==""'
    return f"""@echo off
chcp 65001 > nul
set "ALVO={alvo}"
set "RES={resultado}"
:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait_loop
)
timeout /t 1 /nobreak >nul
copy /Y "{atual}" "{bak}" >nul
if errorlevel 1 goto sem_backup
move /Y "{novo}" "{atual}" >nul
if errorlevel 1 goto sem_troca
start "" "{atual}"
set /a T=0
:saude
timeout /t 5 /nobreak >nul
set /a T+=5
set "V="
for /f "usebackq delims=" %%v in (`{confere}`) do set "V=%%v"
if {condicao} goto ok
if %T% LSS {ESPERA_DA_SAUDE_S} goto saude
taskkill /F /IM "{atual.name}" >nul 2>&1
timeout /t 3 /nobreak >nul
move /Y "{bak}" "{atual}" >nul
start "" "{atual}"
> "%RES%" echo voltou %ALVO% a versao nova nao respondeu em {ESPERA_DA_SAUDE_S} s
goto fim
:sem_backup
> "%RES%" echo falhou %ALVO% nao foi possivel guardar a copia do executavel atual
del "{novo}" >nul 2>&1
start "" "{atual}"
goto fim
:sem_troca
> "%RES%" echo falhou %ALVO% nao foi possivel trocar o executavel
start "" "{atual}"
goto fim
:ok
> "%RES%" echo ok %ALVO%
:fim
del "%~f0"
"""


def ler_resultado(data_dir: Path) -> tuple[str, str, str] | None:
    """``(desfecho, versao, motivo)`` da última troca, e apaga o arquivo. ``None`` se não houver."""
    caminho = data_dir / RESULTADO
    try:
        texto = caminho.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    caminho.unlink(missing_ok=True)
    partes = texto.split(" ", 2)
    if len(partes) < 2 or partes[0] not in {"ok", "voltou", "falhou"}:
        return None
    return partes[0], partes[1], partes[2] if len(partes) > 2 else ""
