"""Endpoints da identidade visual: upload/get/delete + validações."""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    assert client.post(
        "/api/auth/login", json={"username": user.username, "password": plaintext},
    ).status_code == 200
    return client.cookies.get("mm_csrf") or ""


def test_upload_get_delete_logo(client, db) -> None:
    csrf = _authed(client, db)
    assert client.get("/api/branding/status").json() == {"logo": False, "favicon": False}

    up = client.post(
        "/api/branding/logo",
        files={"file": ("logo.png", b"\x89PNGfake-bytes", "image/png")},
        headers={"X-CSRF-Token": csrf},
    )
    assert up.status_code == 200, up.json()

    got = client.get("/api/branding/logo")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert got.content == b"\x89PNGfake-bytes"
    assert client.get("/api/branding/status").json()["logo"] is True

    dele = client.delete("/api/branding/logo", headers={"X-CSRF-Token": csrf})
    assert dele.status_code == 200
    assert client.get("/api/branding/logo").status_code == 404


def test_upload_rejeita_extensao(client, db) -> None:
    csrf = _authed(client, db)
    r = client.post(
        "/api/branding/logo",
        files={"file": ("config.txt", b"abc", "text/plain")},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_kind_invalido_404(client, db) -> None:
    _authed(client, db)
    assert client.get("/api/branding/banner").status_code == 404


def test_upload_exige_csrf(client, db) -> None:
    _authed(client, db)
    r = client.post(
        "/api/branding/logo",
        files={"file": ("logo.png", b"x", "image/png")},
    )
    assert r.status_code == 403
