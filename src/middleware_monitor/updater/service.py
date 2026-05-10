"""Periodic update check executed by the scheduler."""

from __future__ import annotations

from packaging.version import Version

from middleware_monitor.core.db import session_factory
from middleware_monitor.core.logging import get_logger
from middleware_monitor.settings import get_settings
from middleware_monitor.updater.client import GithubReleasesClient, Release
from middleware_monitor.version import __version__

log = get_logger("updater")

_state: dict[str, object] = {
    "last_check_at": None,
    "last_check_ok": False,
    "available": None,  # type: Release | None
    "channel": None,
    "auto_update": True,
}


def get_state() -> dict[str, object]:
    return dict(_state)


async def run_update_check() -> Release | None:
    settings = get_settings()
    client = GithubReleasesClient(settings.update_repo)
    try:
        release = await client.latest_for_channel(
            channel=settings.update_channel,
            current=Version(__version__),
        )
        _state["last_check_at"] = __import__("datetime").datetime.utcnow().replace(microsecond=0)
        _state["last_check_ok"] = True
        _state["available"] = release
        _state["channel"] = settings.update_channel
        if release is None:
            log.info("update_check", result="up_to_date", current=__version__)
        else:
            log.info("update_check", result="available", target=str(release.version))
        return release
    except Exception as exc:  # noqa: BLE001
        _state["last_check_ok"] = False
        log.warning("update_check_failed", error=str(exc))
        return None
