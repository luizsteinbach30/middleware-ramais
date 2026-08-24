"""HTML pages — server-rendered shells. Dynamic data is loaded by JS modules."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, DictLoader
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import get_session
from middleware_monitor.core import resources
from middleware_monitor.core.models import Session as SessionModel
from middleware_monitor.core.models import User
from middleware_monitor.core.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    issue_csrf_token,
)
from middleware_monitor.settings import get_settings
from middleware_monitor.version import __version__

router = APIRouter()


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    """Ambiente Jinja da aplicação — montado uma vez, não a cada request.

    Montar um ``Jinja2Templates`` cria um ``Environment`` novo, com cache de
    compilação novo: fazer isso por request obrigava **toda** tela a recompilar
    o template do zero, toda vez. Medido em 2026-08-24 (ver
    ``docs/design/PERF_BASELINE.md``): era o maior custo do caminho de request,
    acima de qualquer consulta ao banco — ``/config`` caiu de 11,7 ms para
    2,2 ms, ``/devices`` de 9,8 ms para 2,2 ms.

    Memorizar **não** congela o template em disco: o ``auto_reload`` do Jinja
    continua ligado, então editar um template em desenvolvimento e dar refresh
    segue funcionando sem reiniciar o processo.
    """
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    loader = templates.env.loader
    if loader is not None:
        # O disco continua sendo a fonte; o cache em memória só entra quando o
        # arquivo não está mais lá. É o que mantém a tela de pé quando o
        # diretório de extração do .exe é esvaziado com o app no ar
        # (ver core/resources.py).
        #
        # O ``DictLoader`` é montado mesmo com o cache ainda vazio, e de
        # propósito: ele guarda a referência do dicionário que ``preload()``
        # preenche depois. Condicionar ao cache já estar cheio faria a rede de
        # segurança depender da ordem entre a primeira chamada daqui e o
        # ``preload()`` — ordem que, com esta função memorizada, valeria para
        # sempre.
        templates.env.loader = ChoiceLoader(
            [loader, DictLoader(resources.templates_cache())]
        )
    return templates


def _maybe_user(
    request: Request,
    db: DBSession,
    cookie: str | None,
) -> User | None:
    if not cookie:
        return None
    from middleware_monitor.core.security import _decode_cookie

    raw = _decode_cookie(cookie)
    if not raw:
        return None
    sess = db.get(SessionModel, raw)
    if sess is None:
        return None
    user = db.get(User, sess.user_id)
    return user


def _ensure_csrf(request: Request) -> str:
    token = request.cookies.get(CSRF_COOKIE)
    if not token:
        token = issue_csrf_token()
        request.state._issue_csrf = token
    return token


def _render(
    templates: Jinja2Templates,
    request: Request,
    name: str,
    *,
    user: User | None,
    extra: dict | None = None,
) -> HTMLResponse:
    csrf = _ensure_csrf(request)
    ctx = {
        "user": user,
        "csrf_token": csrf,
        "version": __version__,
        "page": name.rsplit(".", 1)[0],
    }
    if extra:
        ctx.update(extra)
    response = templates.TemplateResponse(request, name, ctx)
    if getattr(request.state, "_issue_csrf", None):
        s = get_settings()
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            httponly=False,
            samesite="lax",
            secure=s.cookie_secure,
            max_age=12 * 60 * 60,
            path="/",
        )
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: DBSession = Depends(get_session),
) -> Response:
    user = _maybe_user(request, db, cookie)
    if user:
        return RedirectResponse("/", status_code=302)
    return _render(get_templates(), request, "login.html", user=None)


def _require_user(
    request: Request,
    cookie: str | None,
    db: DBSession,
) -> User:
    user = _maybe_user(request, db, cookie)
    if user is None:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


@router.get("/", response_class=HTMLResponse)
def dashboard_page(
    request: Request,
    cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: DBSession = Depends(get_session),
) -> Response:
    user = _maybe_user(request, db, cookie)
    if user is None:
        return RedirectResponse("/login", status_code=302)
    if user.must_change_password:
        return RedirectResponse("/account?force=1", status_code=302)
    return _render(get_templates(), request, "dashboard.html", user=user)


def _page(name: str, route: str) -> None:
    @router.get(route, response_class=HTMLResponse)
    def _handler(
        request: Request,
        cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
        db: DBSession = Depends(get_session),
    ) -> Response:
        user = _maybe_user(request, db, cookie)
        if user is None:
            return RedirectResponse("/login", status_code=302)
        if user.must_change_password and route != "/account":
            return RedirectResponse("/account?force=1", status_code=302)
        return _render(get_templates(), request, name, user=user)

    _handler.__name__ = f"page_{name.replace('.', '_')}"


_page("devices.html", "/devices")
_page("device_detail.html", "/devices/{device_id}")
_page("collections.html", "/collections")
_page("webhook_logs.html", "/webhook-logs")
_page("logs.html", "/logs")
_page("mqtt_live.html", "/mqtt-painel")
_page("mqtt_calls.html", "/mqtt-chamadas")
_page("mqtt_messages.html", "/mqtt-messages")
_page("config.html", "/config")
_page("system_updates.html", "/system/updates")
_page("system_backup.html", "/system/backup")
_page("account.html", "/account")


# --- Configurador de Ramais (extension configurator) ---

_page("extension_configurator/list.html", "/extension-configurator/environments")
_page("extension_configurator/runs.html", "/extension-configurator/runs")


@router.get(
    "/extension-configurator/environments/{ambiente_id}",
    response_class=HTMLResponse,
)
def ec_environment_detail_page(
    request: Request,
    ambiente_id: str,
    cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: DBSession = Depends(get_session),
) -> HTMLResponse:
    user = _maybe_user(request, db, cookie)
    if user is None:
        return RedirectResponse("/login", status_code=302)  # type: ignore[return-value]
    return _render(
        get_templates(), request, "extension_configurator/detail.html",
        user=user,
        extra={"page": "ec_environment_detail", "ambiente_id": ambiente_id},
    )


@router.get(
    "/extension-configurator/environments/{ambiente_id}/config",
    response_class=HTMLResponse,
)
def ec_environment_config_page(
    request: Request,
    ambiente_id: str,
    cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: DBSession = Depends(get_session),
) -> HTMLResponse:
    user = _maybe_user(request, db, cookie)
    if user is None:
        return RedirectResponse("/login", status_code=302)  # type: ignore[return-value]
    return _render(
        get_templates(), request, "extension_configurator/config.html",
        user=user,
        extra={"page": "ec_environment_config", "ambiente_id": ambiente_id},
    )


@router.get(
    "/extension-configurator/runs/{run_id}",
    response_class=HTMLResponse,
)
def ec_run_detail_page(
    request: Request,
    run_id: int,
    cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: DBSession = Depends(get_session),
) -> HTMLResponse:
    user = _maybe_user(request, db, cookie)
    if user is None:
        return RedirectResponse("/login", status_code=302)  # type: ignore[return-value]
    return _render(
        get_templates(), request, "extension_configurator/run_detail.html",
        user=user,
        extra={"page": "ec_run_detail", "run_id": run_id},
    )
