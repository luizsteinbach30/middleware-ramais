"""Periodic update check executed by the scheduler."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from packaging.version import Version

from middleware_monitor.core.logging import get_logger
from middleware_monitor.settings import get_settings
from middleware_monitor.updater.client import (
    GithubReleasesClient,
    Release,
    UpdateMode,
)
from middleware_monitor.version import __version__

log = get_logger("updater")

_state: dict[str, object] = {
    "last_check_at": None,
    "last_check_ok": False,
    "available": None,  # holds a Release once a check finds an update
    "channel": None,
    # Verificação automática ligada? Reflete a config persistida (antes era um
    # literal `True` que a UI exibia como se fosse estado real).
    "auto_update": True,
}


def get_state() -> dict[str, object]:
    return dict(_state)


def set_auto_check(enabled: bool) -> None:
    """Espelha no estado exposto pela API o que está persistido na config."""
    _state["auto_update"] = enabled


def _configured_channel() -> str:
    """Canal da tela; `.env` é só fallback (e último recurso se o DB falhar)."""
    from middleware_monitor.core.db import session_factory
    from middleware_monitor.domain.config.update_settings import load_update_settings

    try:
        with session_factory() as db:
            return load_update_settings(db).channel
    except Exception as exc:  # checagem não pode cair por causa do DB
        log.warning("update_channel_fallback_env", error=str(exc))
        return get_settings().update_channel


async def run_update_check() -> Release | None:
    settings = get_settings()
    channel = _configured_channel()
    client = GithubReleasesClient(
        settings.update_repo,
        token=settings.effective_update_token,
    )
    mode: UpdateMode = "standalone" if getattr(sys, "frozen", False) else "legacy"
    try:
        release = await client.latest_for_channel(
            channel=channel,
            current=Version(__version__),
            mode=mode,
        )
        _state["last_check_at"] = datetime.now(UTC).replace(microsecond=0, tzinfo=None)
        _state["last_check_ok"] = True
        _state["available"] = release
        _state["channel"] = channel
        if release is None:
            log.info("update_check", result="up_to_date", current=__version__)
        else:
            log.info("update_check", result="available", target=str(release.version))
        return release
    except Exception as exc:
        _state["last_check_ok"] = False
        log.warning("update_check_failed", error=str(exc))
        return None
