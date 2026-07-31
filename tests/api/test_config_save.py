"""Config API — saves parciais, validação e CSRF.

Nota v2.7.0: os campos uscall_host/uscall_token saíram do ``AppConfigUpdate``
— servidores USCall agora são geridos pelo CRUD ``/api/config/uscall-servers``
(ver ``test_uscall_servers_api.py``).
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


def test_partial_save_client_code(client, db) -> None:
    csrf = _authed(client, db)
    r = client.put(
        "/api/config",
        json={"client_code": "cliente-x"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["client_code"] == "cliente-x"


def test_uscall_kv_legado_ignorado_no_put(client, db) -> None:
    """Compat: PUT com os campos antigos não quebra (extra=ignore), mas também
    não escreve mais no KV — a fonte da verdade é a tabela uscall_servers."""
    csrf = _authed(client, db)
    r = client.put(
        "/api/config",
        json={"uscall_host": "nao-deve-persistir.test", "uscall_token": "x"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    cfg = client.get("/api/config").json()
    assert cfg["uscall_host"] == ""          # KV intocado
    assert cfg["uscall_servers"] == []


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
    r = client.put("/api/config", json={"client_code": "x"})
    assert r.status_code == 403
    assert r.json()["detail"] == "csrf_invalid"


def test_webhook_secret_round_trip(client, db) -> None:
    csrf = _authed(client, db)
    client.put(
        "/api/config",
        json={"webhooks": {"devices": {"token": "supersecret"}}},
        headers={"X-CSRF-Token": csrf},
    )
    cfg = client.get("/api/config").json()
    assert cfg["webhooks"]["devices"]["token"] == "set"

    client.put(
        "/api/config",
        json={"webhooks": {"devices": {"token": ""}}},
        headers={"X-CSRF-Token": csrf},
    )
    cfg = client.get("/api/config").json()
    assert cfg["webhooks"]["devices"]["token"] is None
