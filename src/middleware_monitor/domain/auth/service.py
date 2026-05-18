"""Authentication service — login, password change, lockouts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from middleware_monitor.core.logging import get_logger
from middleware_monitor.core.models import LoginAttempt, User
from middleware_monitor.core.security import (
    hash_password,
    verify_password,
)

log = get_logger("auth")

LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW = timedelta(minutes=10)
LOCKOUT_DURATION = timedelta(minutes=5)
PASSWORD_MIN_LEN = 12


class AuthError(Exception):
    pass


class InvalidCredentials(AuthError):
    pass


class TooManyAttempts(AuthError):
    pass


class WeakPassword(AuthError):
    pass


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def is_strong_password(p: str) -> bool:
    if len(p) < PASSWORD_MIN_LEN:
        return False
    has_letter = any(c.isalpha() for c in p)
    has_digit = any(c.isdigit() for c in p)
    return has_letter and has_digit


def _record_attempt(db: DBSession, ip: str, username: str | None, success: bool) -> None:
    db.add(LoginAttempt(ip=ip, username=username, success=success, timestamp=_now()))
    db.commit()


def _recent_failures(db: DBSession, ip: str) -> int:
    since = _now() - LOCKOUT_WINDOW
    return int(
        db.scalar(
            select(func.count(LoginAttempt.id)).where(
                LoginAttempt.ip == ip,
                LoginAttempt.timestamp >= since,
                LoginAttempt.success.is_(False),
            )
        )
        or 0
    )


def authenticate(db: DBSession, *, username: str, password: str, ip: str) -> User:
    if _recent_failures(db, ip) >= LOCKOUT_THRESHOLD:
        log.warning("auth_lockout", ip=ip)
        raise TooManyAttempts

    user = db.scalar(select(User).where(User.username == username))
    # Constant-time decoy hash to avoid user-enumeration timing.
    decoy_hash = "$2b$12$AbCDeFGhIjKlMnOpQrStUOIAYL4l3Bj/iCk9lHWX2YZl9MzApN0Mu"
    if user is None:
        verify_password(password, decoy_hash)
        _record_attempt(db, ip, username, success=False)
        raise InvalidCredentials

    if user.locked_until and user.locked_until > _now():
        raise TooManyAttempts

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= LOCKOUT_THRESHOLD:
            user.locked_until = _now() + LOCKOUT_DURATION
        _record_attempt(db, ip, username, success=False)
        db.commit()
        raise InvalidCredentials

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _now()
    _record_attempt(db, ip, username, success=True)
    db.commit()
    return user


def change_password(db: DBSession, user: User, *, current: str, new_password: str) -> None:
    if not verify_password(current, user.password_hash):
        raise InvalidCredentials
    if not is_strong_password(new_password):
        raise WeakPassword
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.commit()
    log.info("password_changed", user_id=user.id)


DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


def bootstrap_admin(
    db: DBSession,
    *,
    username: str = DEFAULT_ADMIN_USERNAME,
    password: str | None = None,
) -> tuple[User, str]:
    """Create the initial admin if missing. Returns ``(user, plaintext_password)``.

    The default credentials are ``admin`` / ``admin``. The user is created with
    ``must_change_password=True`` so the first successful login is forced to
    rotate the password before doing anything else (mínimo 12 caracteres com
    letras e números — ver ``is_strong_password``).
    """
    user = db.scalar(select(User).where(User.username == username))
    if user is not None:
        return user, ""

    plaintext = password or DEFAULT_ADMIN_PASSWORD
    user = User(
        username=username,
        password_hash=hash_password(plaintext),
        role="admin",
        must_change_password=True,
        failed_login_count=0,
        created_at=_now(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, plaintext
