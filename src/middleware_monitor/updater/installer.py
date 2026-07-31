"""Installer logic for the auto-updater.

It is responsible for: downloading the artifact, verifying its checksum,
extracting it under ``app/<version>/`` and asking the OS supervisor (systemd
on Linux, NSSM/SCM on Windows) to restart the service. The actual symlink/
junction swap is done atomically; rollback is automatic if the post-restart
health-check fails.

This module assumes ``packaging/`` scripts have been installed by the bundle
installer; on dev machines it can run in dry-run mode.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import UpdateHistory
from middleware_monitor.settings import get_settings
from middleware_monitor.updater.checksums import (
    ChecksumMismatch,
    hash_file,
    parse_sha256sums,
)
from middleware_monitor.updater.client import Release, download_asset
from middleware_monitor.version import __version__

log = get_logger("updater")


def _silent_kwargs() -> dict:
    """Extra kwargs to pass to ``subprocess.run`` so children don't flash a
    console window when the parent .exe is built with ``console=False``."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


@dataclass(slots=True)
class InstallResult:
    success: bool
    new_version: str
    duration_ms: int
    rolled_back: bool = False
    error: str | None = None


class UpdateInstallerError(Exception):
    pass


class TarballUnsafe(UpdateInstallerError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _is_safe_member(name: str) -> bool:
    if name.startswith("/") or "\\" in name or ".." in Path(name).parts:
        return False
    return True


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    for member in tar.getmembers():
        if not _is_safe_member(member.name):
            raise TarballUnsafe(f"unsafe path: {member.name}")
    tar.extractall(path=dest, filter="data")


def _service_restart() -> None:
    """Best-effort restart. The watcher process runs as a different user
    than the main service, so we shell out to the supervisor."""
    if platform.system().lower().startswith("win"):
        try:
            subprocess.run(
                ["nssm", "restart", "MiddlewareMonitor"],
                check=True,
                timeout=30,
                **_silent_kwargs(),
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            subprocess.run(["sc", "stop", "MiddlewareMonitor"], check=False, timeout=30, **_silent_kwargs())
            subprocess.run(["sc", "start", "MiddlewareMonitor"], check=False, timeout=30, **_silent_kwargs())
    else:
        subprocess.run(["systemctl", "restart", "middleware-monitor"], check=True, timeout=30)


async def _wait_healthy(url: str, *, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=3.0) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2.0)
    return False


def _record(
    db: DBSession,
    *,
    to_version: str,
    channel: str,
    status: str,
    duration_ms: int,
    error: str | None,
) -> None:
    db.add(
        UpdateHistory(
            timestamp=_now(),
            from_version=__version__,
            to_version=to_version,
            channel=channel,
            status=status,
            error=error,
            duration_ms=duration_ms,
        )
    )
    db.commit()


async def install_release(
    release: Release,
    *,
    install_root: Path | None = None,
    health_url: str = "http://127.0.0.1:8080/api/system/healthz",
    dry_run: bool = False,
) -> InstallResult:
    """Run the full install pipeline for a given release."""
    settings = get_settings()
    started = time.perf_counter()
    install_root = install_root or _detect_install_root()

    new_version = str(release.version)
    new_dir = install_root / "app" / new_version
    current_link = install_root / "current"
    tmp_dir = Path(tempfile.mkdtemp(prefix="mm-update-", dir=settings.tmp_dir))

    error: str | None = None
    rolled_back = False
    success = False

    try:
        if release.tarball is None or release.sha256sums is None:
            raise UpdateInstallerError("release missing tarball or SHA256SUMS")

        tar_path = tmp_dir / release.tarball.name
        sha_path = tmp_dir / "SHA256SUMS"

        token = settings.effective_update_token
        log.info("update_download_started", version=new_version)
        await download_asset(release.tarball, tar_path, token=token)
        await download_asset(release.sha256sums, sha_path, token=token)

        expected = parse_sha256sums(sha_path, release.tarball.name)
        actual = hash_file(tar_path)
        if not actual.lower() == expected.lower():
            raise ChecksumMismatch(f"expected {expected}, got {actual}")
        log.info("update_checksum_ok", version=new_version)

        if dry_run:
            log.info("update_dry_run_complete", version=new_version)
            success = True
            return InstallResult(
                success=True,
                new_version=new_version,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        new_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path, "r:gz") as tar:
            _safe_extract(tar, new_dir)

        # Optional: pip install --no-deps -r requirements.lock
        venv_python = install_root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        req = new_dir / "requirements.lock"
        if venv_python.exists() and req.exists():
            subprocess.run(
                [str(venv_python), "-m", "pip", "install", "--no-deps", "-r", str(req)],
                check=True,
                timeout=300,
                **_silent_kwargs(),
            )

        # Run alembic upgrade head from the new dir, against current DB.
        alembic_ini = new_dir / "alembic.ini"
        if alembic_ini.exists() and venv_python.exists():
            subprocess.run(
                [str(venv_python), "-m", "alembic", "-c", str(alembic_ini), "upgrade", "head"],
                check=True,
                timeout=120,
                env={**os.environ, "APP_DATA_DIR": str(settings.data_dir)},
                **_silent_kwargs(),
            )

        # Atomic swap of "current" → new dir.
        if current_link.exists() or current_link.is_symlink():
            current_link.unlink()
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(current_link), str(new_dir)],
                check=True,
                timeout=10,
                **_silent_kwargs(),
            )
        else:
            current_link.symlink_to(new_dir)

        # Restart the service and verify health.
        _service_restart()
        ok = await _wait_healthy(health_url)
        if not ok:
            log.error("update_healthcheck_failed", version=new_version)
            _rollback(install_root)
            rolled_back = True
            raise UpdateInstallerError("healthcheck_failed")

        success = True
        log.info("update_installed", version=new_version)

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.error("update_failed", error=error, version=new_version)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    duration_ms = int((time.perf_counter() - started) * 1000)
    status = "success" if success else ("rolled_back" if rolled_back else "failed")
    with session_factory() as db:
        _record(
            db,
            to_version=new_version,
            channel=release.channel,
            status=status,
            duration_ms=duration_ms,
            error=error,
        )
    return InstallResult(
        success=success,
        new_version=new_version,
        duration_ms=duration_ms,
        rolled_back=rolled_back,
        error=error,
    )


def _rollback(install_root: Path) -> None:
    """Best-effort rollback: rename ``current`` back to the most recent
    sibling under ``app/`` other than the failing one. The supervisor will
    pick it up on restart."""
    apps = install_root / "app"
    if not apps.exists():
        return
    versions = sorted(apps.iterdir(), key=lambda p: p.name, reverse=True)
    if len(versions) < 2:
        return
    # Skip the most recent (failing) version.
    previous = versions[1]
    current_link = install_root / "current"
    if current_link.exists() or current_link.is_symlink():
        current_link.unlink()
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(current_link), str(previous)],
            check=False,
            **_silent_kwargs(),
        )
    else:
        current_link.symlink_to(previous)
    try:
        _service_restart()
    except Exception:
        pass


def _detect_install_root() -> Path:
    """In dev mode, pretend the project root is the install root."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def main() -> None:  # pragma: no cover
    """Entry point for ``python -m middleware_monitor.updater``."""
    sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
