"""FastAPI application factory + lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from middleware_monitor.api import (
    auth as api_auth,
)
from middleware_monitor.api import (
    collections as api_collections,
)
from middleware_monitor.api import (
    config as api_config,
)
from middleware_monitor.api import (
    dashboard as api_dashboard,
)
from middleware_monitor.api import (
    devices as api_devices,
)
from middleware_monitor.api import (
    logs as api_logs,
)
from middleware_monitor.api import (
    system as api_system,
)
from middleware_monitor.api import (
    webhooks as api_webhooks,
)
from middleware_monitor.core.db import init_engine
from middleware_monitor.core.logging import configure_logging, get_logger
from middleware_monitor.core.scheduler import (
    get_scheduler,
    shutdown as scheduler_shutdown,
    start as scheduler_start,
)
from middleware_monitor.jobs import register_all
from middleware_monitor.settings import get_settings
from middleware_monitor.version import __version__
from middleware_monitor.web import pages as web_pages

log = get_logger("app")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("app_starting", version=__version__, host=settings.host, port=settings.port)
        init_engine()
        register_all(get_scheduler())
        scheduler_start()
        yield
        scheduler_shutdown(wait=False)
        log.info("app_stopped")

    app = FastAPI(
        title="Middleware USCall Monitor",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(SecurityHeadersMiddleware)

    static_dir = Path(__file__).parent / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # API routers
    for r in (
        api_auth.router,
        api_config.router,
        api_devices.router,
        api_collections.router,
        api_webhooks.router,
        api_logs.router,
        api_dashboard.router,
        api_system.router,
    ):
        app.include_router(r)

    # Web routers (HTML)
    app.include_router(web_pages.router)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:  # noqa: ANN202
        log.error("unhandled_exception", error=type(exc).__name__, message=str(exc))
        return JSONResponse({"detail": "internal_error"}, status_code=500)

    return app


_app: FastAPI | None = None


def get_app() -> FastAPI:
    global _app
    if _app is None:
        _app = create_app()
    return _app


asgi: ASGIApp = get_app()
