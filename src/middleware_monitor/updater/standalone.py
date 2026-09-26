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
4. Write a small ``apply_update.ps1`` helper that:
   - waits for every process of the running ``.exe`` to terminate (and ends
     them after 60 s),
   - keeps the running ``.exe`` as ``<nome>.exe.bak``,
   - moves the new ``.exe`` over the running one and re-launches it,
   - **confere a saúde** (ADR 0008): ``/api/system/healthz`` tem de responder com a
     versão nova em até 150 s; senão encerra a nova, devolve o ``.bak`` e sobe a
     antiga — sem isso, uma release que não abre deixava o cliente sem middleware
     até alguém ir lá,
   - grava o desfecho em ``update_result.txt`` (lido no boot seguinte e mandado
     ao NOC) e se apaga.
5. Spawn the helper with a hidden console (``disparar_ajudante``) so it survives
   our exit without opening a single window. Only one helper runs at a time
   (``update.lock`` + a named mutex).
6. Caller is expected to terminate the process so the helper can swap the
   binary (Windows refuses to overwrite a running ``.exe``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
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
    otherwise the helper ends it after 60 s.
    """
    if not sys.platform.startswith("win"):
        raise UpdateError("standalone update is only supported on Windows")
    if not getattr(sys, "frozen", False):
        raise UpdateError("standalone update only runs from the PyInstaller .exe")
    if not sha_url:
        raise UpdateError("release missing SHA256SUMS (required for integrity check)")

    tmp_dir = data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    trava = tmp_dir / TRAVA
    tomar_trava(trava)
    new_exe = tmp_dir / asset_name

    log.info("update_download_started", asset=asset_name, url=asset_url)
    try:
        download_asset_sync(asset_url, new_exe, token=token)
    except Exception as exc:
        trava.unlink(missing_ok=True)
        raise UpdateError(f"download failed: {exc}") from exc
    log.info("update_download_done", path=str(new_exe), bytes=new_exe.stat().st_size)

    sums_path = tmp_dir / "SHA256SUMS"
    try:
        download_asset_sync(sha_url, sums_path, token=token)
        verify_file(new_exe, sums_path, target_name=asset_name)
    except Exception as exc:
        new_exe.unlink(missing_ok=True)
        sums_path.unlink(missing_ok=True)
        trava.unlink(missing_ok=True)
        raise UpdateError(f"checksum verification failed: {exc}") from exc
    log.info("update_checksum_ok", asset=asset_name)

    current_exe = current_exe or Path(sys.executable).resolve()
    helper = tmp_dir / AJUDANTE
    helper.write_text(
        script_do_ajudante(
            novo=new_exe,
            atual=current_exe,
            alvo=versao_esperada or "",
            porta=porta or _porta_local(),
            resultado=data_dir / RESULTADO,
            trava=trava,
        ),
        encoding="utf-8-sig",  # o PowerShell 5.1 só lê acento em arquivo com BOM
    )
    try:
        disparar_ajudante(helper)
    except OSError:
        trava.unlink(missing_ok=True)
        raise
    log.info("update_helper_spawned", helper=str(helper), exe=str(current_exe))
    return new_exe


RESULTADO = "update_result.txt"
AJUDANTE = "apply_update.ps1"
TRAVA = "update.lock"
ESPERA_DA_SAUDE_S = 150
ESPERA_DO_ENCERRAMENTO_S = 60
TRAVA_VENCE_S = 15 * 60

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def tomar_trava(trava: Path) -> None:
    """Uma troca por vez. Dois cliques (ou botão local + pedido do NOC) disparavam dois
    ajudantes disputando o mesmo ``.exe``. A trava vence sozinha: um ajudante que morreu
    no meio não pode impedir a próxima atualização para sempre."""
    try:
        idade: float | None = time.time() - trava.stat().st_mtime
    except OSError:
        idade = None
    if idade is not None and idade < TRAVA_VENCE_S:
        raise UpdateError("já há uma atualização em andamento neste middleware")
    trava.write_text(str(os.getpid()), encoding="ascii")


def disparar_ajudante(helper: Path) -> subprocess.Popen[bytes]:
    """Sobe o ajudante sem janela nenhuma.

    ``CREATE_NO_WINDOW`` dá ao PowerShell um console **oculto**, que ele e o que ele
    chamar herdam. Até a 2.14.1 o ajudante era um ``.bat`` com ``DETACHED_PROCESS``
    (sem console): cada ``tasklist``/``timeout``/``powershell`` abria uma janela
    própria, e o ``timeout`` sem console falha na hora (código 125) — as esperas do
    ``.bat`` giravam sem pausa. Era o "abre telas do cmd e entra em laço" do campo.
    """
    comando = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(helper),
    ]
    base = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        # Fora do job do processo pai: um serviço ou terminal que mata o job ao fechar
        # levaria o ajudante junto, no meio da troca. Job que não deixa sair recusa.
        return subprocess.Popen(
            comando,
            creationflags=base | CREATE_BREAKAWAY_FROM_JOB,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return subprocess.Popen(
            comando,
            creationflags=base,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _porta_local() -> int:
    from middleware_monitor.settings import get_settings

    return int(get_settings().port)


def _ps(texto: str | Path) -> str:
    """Literal de PowerShell entre aspas simples (nada se expande dentro)."""
    return "'" + str(texto).replace("'", "''") + "'"


def script_do_ajudante(
    *, novo: Path, atual: Path, alvo: str, porta: int, resultado: Path, trava: Path
) -> str:
    """O ``.ps1`` que troca o executável depois que este processo sai.

    Tudo acontece dentro do próprio PowerShell — espera, cópia, saúde —, sem chamar
    programa de console nenhum. Espera **todos** os processos daquele ``.exe`` (o
    PyInstaller onefile roda dois: o carregador e o Python) e, passado o prazo, os
    encerra. Sem ``alvo`` (versão desconhecida) a saúde aceita qualquer versão que
    responda: ainda prova que o executável novo abre.
    """
    bak = atual.with_name(atual.name + ".bak")
    return f"""$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$novo = {_ps(novo)}
$atual = {_ps(atual)}
$bak = {_ps(bak)}
$alvo = {_ps(alvo)}
$res = {_ps(resultado)}
$trava = {_ps(trava)}
$saude = 'http://127.0.0.1:{int(porta)}/api/system/healthz'

function Fim([string]$texto) {{
  [IO.File]::WriteAllText($res, $texto, (New-Object Text.UTF8Encoding $false))
}}
function DoExe {{
  @(Get-Process -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq $atual }})
}}
function Encerrar {{
  $prazo = (Get-Date).AddSeconds({ESPERA_DO_ENCERRAMENTO_S})
  while ((DoExe).Count -gt 0 -and (Get-Date) -lt $prazo) {{ Start-Sleep -Milliseconds 500 }}
  DoExe | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 1
}}
function Trocar([string]$de, [string]$para) {{
  for ($i = 0; $i -lt 10; $i++) {{
    try {{ Move-Item -LiteralPath $de -Destination $para -Force -ErrorAction Stop; return $true }}
    catch {{ Start-Sleep -Seconds 1 }}
  }}
  return $false
}}
function Subir {{
  Start-Process -FilePath $atual -WorkingDirectory (Split-Path -Parent $atual) | Out-Null
}}

$mutex = New-Object Threading.Mutex($false, 'Global\\MiddlewareMonitorUpdate')
if (-not $mutex.WaitOne(0)) {{ exit 0 }}
try {{
  Encerrar
  try {{ Copy-Item -LiteralPath $atual -Destination $bak -Force -ErrorAction Stop }}
  catch {{
    Fim "falhou $alvo nao foi possivel guardar a copia do executavel atual"
    Remove-Item -LiteralPath $novo -Force -ErrorAction SilentlyContinue
    Subir
    return
  }}
  if (-not (Trocar $novo $atual)) {{
    Fim "falhou $alvo nao foi possivel trocar o executavel"
    Subir
    return
  }}
  Subir
  $prazo = (Get-Date).AddSeconds({ESPERA_DA_SAUDE_S})
  while ((Get-Date) -lt $prazo) {{
    Start-Sleep -Seconds 5
    $v = ''
    try {{ $v = [string](Invoke-RestMethod -UseBasicParsing -TimeoutSec 4 -Uri $saude).version }} catch {{ }}
    if (($alvo -and $v -eq $alvo) -or (-not $alvo -and $v)) {{
      Fim "ok $alvo"
      return
    }}
  }}
  DoExe | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
  if (Trocar $bak $atual) {{ Subir }}
  Fim "voltou $alvo a versao nova nao respondeu em {ESPERA_DA_SAUDE_S} s"
}}
finally {{
  Remove-Item -LiteralPath $trava -Force -ErrorAction SilentlyContinue
  $mutex.ReleaseMutex()
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}}
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
