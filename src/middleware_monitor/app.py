"""FastAPI application factory + lifespan."""

from __future__ import annotations

import os
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
    branding as api_branding,
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
    extension_configurator as api_extension_configurator,
)
from middleware_monitor.api import (
    logs as api_logs,
)
from middleware_monitor.api import (
    mqtt as api_mqtt,
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
)
from middleware_monitor.core.scheduler import (
    shutdown as scheduler_shutdown,
)
from middleware_monitor.core.scheduler import (
    start as scheduler_start,
)
from middleware_monitor.domain.mqtt.service import get_ingestor
from middleware_monitor.jobs import register_all
from middleware_monitor.settings import get_settings
from middleware_monitor.version import __version__
from middleware_monitor.web import pages as web_pages

log = get_logger("app")


# CSP em modo *Report-Only*: o navegador NÃO bloqueia nada — apenas reporta
# violações no console. Serve para medir a superfície atual (Tailwind Play CDN
# inline, jsdelivr, estilos inline) antes de, no futuro, endurecer e impor uma
# CSP real. A política reflete o uso atual para não gerar ruído, mas já fixa
# object-src/base-uri/frame-ancestors (não usados → seguro).
_CSP_REPORT_ONLY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data:; "
    "font-src 'self' data: https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        path = request.url.path
        if path.startswith("/api"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif path.startswith("/static"):
            # Assets revalidam a cada load (ETag/Last-Modified → 304 quando não
            # mudou, refetch quando muda). Evita servir JS/CSS/módulos antigos
            # do cache do navegador após um deploy/reinício.
            response.headers.setdefault("Cache-Control", "no-cache")
        else:
            # Só nos documentos HTML (não /api, não /static).
            response.headers.setdefault(
                "Content-Security-Policy-Report-Only", _CSP_REPORT_ONLY,
            )
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    # Segurança: o default 'change-me' assina as sessões com chave conhecida.
    # No modo desktop o secret.key é gerado automaticamente; em modo servidor/
    # headless avisa alto. Hard-fail só quando APP_REQUIRE_SECRET_KEY=1, para
    # não quebrar quem hoje sobe com o default.
    if settings.secret_key == "change-me":
        if settings.require_secret_key:
            raise RuntimeError(
                "APP_SECRET_KEY nao configurado (valor default 'change-me'). "
                "Defina APP_SECRET_KEY ou remova APP_REQUIRE_SECRET_KEY."
            )
        log.warning(
            "insecure_secret_key",
            detail=(
                "APP_SECRET_KEY usando default 'change-me' — sessoes assinadas com "
                "chave conhecida. Defina APP_SECRET_KEY em producao."
            ),
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("app_starting", version=__version__, host=settings.host, port=settings.port)
        init_engine()
        register_all(get_scheduler())
        scheduler_start()
        # Coletor MQTT: conexao persistente, entao vive no lifespan e nao no
        # scheduler (que serve para trabalho periodico). Falha ao subir nao
        # pode derrubar a aplicacao inteira.
        ingestor = get_ingestor()
        try:
            await ingestor.start()
        except Exception as exc:
            log.error("mqtt_ingest_start_failed", error=type(exc).__name__, message=str(exc))
        yield
        await ingestor.stop()
        scheduler_shutdown(wait=False)
        log.info("app_stopped")

    # Swagger UI + raw OpenAPI ship disabled by default. Since v2.1.4 the app
    # binds on 0.0.0.0 so anybody on the LAN could otherwise enumerate every
    # endpoint without authenticating. Operators that need them during local
    # development can re-enable with APP_EXPOSE_DOCS=1.
    docs_enabled = os.environ.get("APP_EXPOSE_DOCS", "0").lower() in ("1", "true", "yes")
    app = FastAPI(
        title="Middleware USCall Monitor",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
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
        api_extension_configurator.router,
        api_branding.router,
        api_mqtt.router,
    ):
        app.include_router(r)

    # Web routers (HTML)
    app.include_router(web_pages.router)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
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
