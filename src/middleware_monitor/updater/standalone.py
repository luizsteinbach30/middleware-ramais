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
   - moves the new ``.exe`` over the running one,
   - re-launches the new ``.exe``,
   - deletes itself.
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
        f"""@echo off
chcp 65001 > nul
:wait_loop
tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait_loop
)
timeout /t 1 /nobreak >nul
move /Y "{new_exe}" "{current_exe}" >nul
start "" "{current_exe}"
del "%~f0"
""",
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
