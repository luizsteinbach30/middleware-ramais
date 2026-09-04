def test_healthz_no_auth(client) -> None:
    r = client.get("/api/system/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz_returns_status(client) -> None:
    r = client.get("/api/system/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "degraded"}


def _admin_csrf(client, db) -> str:
    from middleware_monitor.domain.auth.service import bootstrap_admin

    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def test_update_em_modo_systemd_deixa_o_pedido_para_a_unidade(client, db, monkeypatch, tmp_path) -> None:
    """Linux pelo .run: o serviço não instala nada — grava update.request e a
    unidade middleware-monitor-update.path faz o resto como root."""
    import pytest
    from packaging.version import Version

    from middleware_monitor.settings import get_settings
    from middleware_monitor.updater.client import Release

    release = Release(
        tag="v9.9.9", version=Version("9.9.9"), channel="stable", published_at="",
        notes="", assets=[], tarball=None, sha256sums=None, sha256sums_sig=None,
    )
    monkeypatch.setattr(get_settings(), "update_mode", "systemd")
    monkeypatch.setattr("middleware_monitor.api.system.get_state", lambda: {"available": release})
    monkeypatch.setattr(
        "middleware_monitor.api.system.spawn",
        lambda *_a, **_k: pytest.fail("o caminho legado (tarball no próprio processo) não pode rodar"),
    )
    csrf = _admin_csrf(client, db)

    r = client.post("/api/system/update", headers={"X-CSRF-Token": csrf})

    assert r.status_code == 200, r.json()
    assert r.json() == {"ok": True, "mode": "systemd", "started_for": "9.9.9"}
    request = tmp_path / "update.request"
    assert request.read_text(encoding="utf-8") == "9.9.9\n"
    assert not (tmp_path / "update.request.tmp").exists()
