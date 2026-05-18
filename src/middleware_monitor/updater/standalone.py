"""Standalone single-exe self-update (Windows).

This is the only update path that is actually wired up for the desktop
build delivered as ``MiddlewareMonitor-X.Y.Z.exe``. The legacy
``installer.py`` (tarball + NSSM / systemd swap) does not apply when the
whole application is a single PyInstaller executable.

Flow:

1. Find the ``MiddlewareMonitor-*.exe`` asset in the GitHub release.
2. Download it to ``%LOCALAPPDATA%/MiddlewareMonitor/tmp``.
3. Write a small ``apply_update.bat`` helper that:
   - waits for the current PID to terminate,
   - moves the new ``.exe`` over the running one,
   - re-launches the new ``.exe``,
   - deletes itself.
4. Spawn the helper detached (no console window) so it survives our exit.
5. Caller is expected to terminate the process so the helper can swap the
   binary (Windows refuses to overwrite a running ``.exe``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from middleware_monitor.core.logging import get_logger

log = get_logger("updater")


class UpdateError(RuntimeError):
    pass


def find_exe_asset(assets: list[dict[str, Any]] | list[Any]) -> dict[str, Any] | None:
    """Return the first asset whose name looks like ``MiddlewareMonitor-X.Y.Z.exe``.

    Accepts either dicts (``{"name": ..., "url": ...}``) or objects with
    ``.name`` / ``.download_url`` attributes (``ReleaseAsset`` from
    ``updater.client``)."""
    for a in assets:
        name = a.get("name") if isinstance(a, dict) else getattr(a, "name", "")
        if not name:
            continue
        if name.startswith("MiddlewareMonitor") and name.endswith(".exe"):
            url = (
                a.get("url") or a.get("download_url")
                if isinstance(a, dict)
                else getattr(a, "download_url", "")
            )
            return {"name": name, "url": url}
    return None


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as response, dest.open("wb") as out:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)


def apply_standalone_update(
    *,
    asset_url: str,
    asset_name: str,
    data_dir: Path,
    current_exe: Path | None = None,
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

    tmp_dir = data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    new_exe = tmp_dir / asset_name

    log.info("update_download_started", asset=asset_name, url=asset_url)
    _download(asset_url, new_exe)
    log.info("update_download_done", path=str(new_exe), bytes=new_exe.stat().st_size)

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
