"""Regression: saving only the USCall host + token must succeed.

Reproduces the user's report where the UI failed to save config because the
JS was posting every field (some empty → 0 → rejected by pydantic ge=1
constraints) and the toast did not surface the real reason.
"""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin


def _authed(client, db):
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def test_partial_save_only_uscall_host_token(client, db) -> None:
    csrf = _authed(client, db)
    r = client.put(
        "/api/config",
        json={"uscall_host": "uscall.test", "uscall_token": "secret-token-123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["uscall_host"] == "uscall.test"
    assert body["uscall_token"] == "set"


def test_save_rejects_invalid_numeric(client, db) -> None:
    csrf = _authed(client, db)
    r = client.put(
        "/api/config",
        json={"ping_concurrency": 0},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("ping_concurrency" in (d.get("loc") or []) for d in detail)


def test_save_without_csrf_returns_403(client, db) -> None:
    _authed(client, db)
    r = client.put("/api/config", json={"uscall_host": "x"})
    assert r.status_code == 403
    assert r.json()["detail"] == "csrf_invalid"


def test_save_persists_and_secret_round_trip(client, db) -> None:
    csrf = _authed(client, db)
    client.put(
        "/api/config",
        json={"uscall_host": "uscall.test", "uscall_token": "supersecret"},
        headers={"X-CSRF-Token": csrf},
    )
    # Re-fetch and the masked field stays "set".
    cfg = client.get("/api/config").json()
    assert cfg["uscall_host"] == "uscall.test"
    assert cfg["uscall_token"] == "set"

    # Clearing the secret with empty string drops it.
    client.put(
        "/api/config",
        json={"uscall_token": ""},
        headers={"X-CSRF-Token": csrf},
    )
    cfg = client.get("/api/config").json()
    assert cfg["uscall_token"] is None
