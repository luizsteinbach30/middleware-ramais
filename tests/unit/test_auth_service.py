import pytest

from middleware_monitor.domain.auth.service import (
    InvalidCredentials,
    TooManyAttempts,
    WeakPassword,
    authenticate,
    bootstrap_admin,
    change_password,
    is_strong_password,
)


def test_password_strength() -> None:
    assert is_strong_password("strongPass123") is True
    assert is_strong_password("short1") is False
    assert is_strong_password("alllettersnodigits") is False


def test_bootstrap_admin_idempotent(db) -> None:
    user1, p1 = bootstrap_admin(db)
    user2, p2 = bootstrap_admin(db)
    assert user1.id == user2.id
    assert p1 != ""
    assert p2 == ""


def test_authenticate_success_failure(db) -> None:
    user, plaintext = bootstrap_admin(db)
    authenticate(db, username=user.username, password=plaintext, ip="1.2.3.4")
    with pytest.raises(InvalidCredentials):
        authenticate(db, username=user.username, password="wrong", ip="1.2.3.4")


def test_lockout_after_threshold(db) -> None:
    user, plaintext = bootstrap_admin(db)
    for _ in range(8):
        try:
            authenticate(db, username=user.username, password="bad", ip="9.9.9.9")
        except (InvalidCredentials, TooManyAttempts):
            pass
    with pytest.raises(TooManyAttempts):
        authenticate(db, username=user.username, password=plaintext, ip="9.9.9.9")


def test_change_password(db) -> None:
    user, plaintext = bootstrap_admin(db)
    change_password(db, user, current=plaintext, new_password="brand-new-pass-123")
    with pytest.raises(InvalidCredentials):
        change_password(db, user, current="wrong", new_password="another-pass-1234")
    with pytest.raises(WeakPassword):
        change_password(db, user, current="brand-new-pass-123", new_password="weak")
