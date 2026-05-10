"""``python -m middleware_monitor`` entrypoint."""

from __future__ import annotations

import uvicorn

from middleware_monitor.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "middleware_monitor.app:asgi",
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
