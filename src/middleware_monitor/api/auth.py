"""Authentication endpoints: login, logout, current user, change password."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.api.deps import get_current_user, get_session, require_csrf
from middleware_monitor.core.models import User
from middleware_monitor.core.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    create_session,
    issue_csrf_token,
    revoke_session,
)
from middleware_monitor.domain.auth.service import (
    InvalidCredentials,
    TooManyAttempts,
    WeakPassword,
    authenticate,
    change_password,
)
from middleware_monitor.settings import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    current: str
    new_password: str


class CurrentUser(BaseModel):
    id: int
    username: str
    role: str
    must_change_password: bool


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_session),
) -> LoginResponse:
    settings = get_settings()
    ip = (request.client.host if request.client else "unknown") or "unknown"
    ua = request.headers.get("User-Agent", "")
    try:
        user = authenticate(db, username=body.username, password=body.password, ip=ip)
    except TooManyAttempts as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="too_many_attempts"
        ) from exc
    except InvalidCredentials as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials") from exc

    cookie = create_session(db, user, ip=ip, user_agent=ua)
    response.set_cookie(
        SESSION_COOKIE,
        cookie,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=12 * 60 * 60,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        issue_csrf_token(),
        httponly=False,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=12 * 60 * 60,
        path="/",
    )
    return LoginResponse(ok=True, must_change_password=user.must_change_password)


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(
    request: Request,
    response: Response,
    db: DBSession = Depends(get_session),
) -> dict[str, bool]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        revoke_session(db, cookie)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=CurrentUser)
def me(user: User = Depends(get_current_user)) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        username=user.username,
        role=user.role,
        must_change_password=user.must_change_password,
    )


@router.post("/change-password", dependencies=[Depends(require_csrf)])
def change_pw(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_session),
) -> dict[str, bool]:
    try:
        change_password(db, user, current=body.current, new_password=body.new_password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=400, detail="invalid_current") from exc
    except WeakPassword as exc:
        raise HTTPException(status_code=400, detail="weak_password") from exc
    return {"ok": True}
