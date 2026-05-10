"""Authentication primitives: password hashing, session creation/validation,
CSRF tokens, and FastAPI dependencies for current user / admin enforcement.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer
import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.db import get_session
from middleware_monitor.core.models import Session as SessionModel
from middleware_monitor.core.models import User
from middleware_monitor.settings import get_settings

SESSION_COOKIE = "mm_session"
CSRF_COOKIE = "mm_csrf"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL = timedelta(hours=12)
_BCRYPT_ROUNDS = 12


def _truncate72(plaintext: str) -> bytes:
    """bcrypt only considers the first 72 bytes; truncate explicitly."""
    return plaintext.encode("utf-8")[:72]


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(_truncate72(plaintext), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_truncate72(plaintext), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().secret_key, salt="mm-session")


def create_session(db: DBSession, user: User, *, ip: str | None, user_agent: str | None) -> str:
    raw = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    sess = SessionModel(
        token=raw,
        user_id=user.id,
        created_at=now,
        expires_at=now + SESSION_TTL,
        user_agent=(user_agent or "")[:255] or None,
        ip=ip,
    )
    db.add(sess)
    db.commit()
    return _serializer().dumps(raw)


def revoke_session(db: DBSession, signed_cookie: str) -> None:
    raw = _decode_cookie(signed_cookie)
    if not raw:
        return
    sess = db.get(SessionModel, raw)
    if sess:
        db.delete(sess)
        db.commit()


def _decode_cookie(cookie_value: str) -> str | None:
    try:
        decoded = _serializer().loads(cookie_value)
        return str(decoded) if decoded else None
    except BadSignature:
        return None


def get_current_user(
    request: Request,
    cookie: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    db: DBSession = Depends(get_session),
) -> User:
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not_authenticated")
    raw = _decode_cookie(cookie)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session")
    sess = db.get(SessionModel, raw)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_session")
    if sess.expires_at <= datetime.now(UTC).replace(tzinfo=None) if sess.expires_at.tzinfo is None else sess.expires_at <= datetime.now(UTC):
        db.delete(sess)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session_expired")
    user = db.get(User, sess.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_gone")
    request.state.session_token = raw
    request.state.user = user
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin_required")
    return user


# --- CSRF ---------------------------------------------------------------------

def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf_invalid")


def require_csrf(_: None = Depends(verify_csrf)) -> None:
    return None


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
