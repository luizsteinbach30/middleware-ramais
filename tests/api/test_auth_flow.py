"""End-to-end auth flow against the FastAPI TestClient."""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin


def _login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_login_failure_returns_401(client) -> None:
    r = _login(client, "admin", "nope")
    assert r.status_code == 401


def test_full_login_logout(client, db) -> None:
    user, plaintext = bootstrap_admin(db)
    r = _login(client, user.username, plaintext)
    assert r.status_code == 200, r.json()
    assert r.json()["must_change_password"] is True
    assert "mm_session" in r.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == user.username

    csrf_token = client.cookies.get("mm_csrf")
    out = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token or ""})
    assert out.status_code == 200


def test_protected_endpoint_requires_auth(client) -> None:
    r = client.get("/api/devices")
    assert r.status_code == 401
