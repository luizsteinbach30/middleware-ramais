"""CRUD de servidores USCall (/api/config/uscall-servers) + teste de conexão."""

from __future__ import annotations

import httpx
import respx

from middleware_monitor.domain.auth.service import bootstrap_admin


def _authed(client, db):
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def _create(client, csrf, **overrides):
    body = {
        "nome": "Matriz",
        "host": "pbx.matriz.com",
        "token": "tok-matriz",
        "verify_ssl": True,
        "enabled": True,
    }
    body.update(overrides)
    return client.post(
        "/api/config/uscall-servers", json=body, headers={"X-CSRF-Token": csrf},
    )


def test_crud_requer_auth_e_csrf(client, db) -> None:
    r = client.get("/api/config/uscall-servers")
    assert r.status_code == 401
    _authed(client, db)
    r = client.post(
        "/api/config/uscall-servers",
        json={"nome": "X", "host": "x", "token": "t"},
    )
    assert r.status_code == 403


def test_create_mascara_token_e_aparece_no_config(client, db) -> None:
    csrf = _authed(client, db)
    r = _create(client, csrf)
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["nome"] == "Matriz"
    assert body["token"] == "set"            # nunca plaintext
    assert "tok-matriz" not in r.text

    cfg = client.get("/api/config").json()
    assert len(cfg["uscall_servers"]) == 1
    assert cfg["uscall_servers"][0]["host"] == "pbx.matriz.com"
    assert cfg["uscall_servers"][0]["token"] == "set"


def test_create_sem_token_rejeitado(client, db) -> None:
    csrf = _authed(client, db)
    assert _create(client, csrf, token="").status_code == 422
    assert _create(client, csrf, token="set").status_code == 422


def test_update_token_set_mantem_o_atual(client, db) -> None:
    csrf = _authed(client, db)
    sid = _create(client, csrf).json()["id"]

    r = client.put(
        f"/api/config/uscall-servers/{sid}",
        json={"nome": "Matriz 2", "host": "pbx2.matriz.com", "token": "set",
              "verify_ssl": False, "enabled": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["nome"] == "Matriz 2"
    assert body["verify_ssl"] is False
    assert body["enabled"] is False
    assert body["token"] == "set"

    # o token original continua decriptável (não foi recifrado/perdido)
    from middleware_monitor.domain.uscall import repository as uscall_repo

    srv = uscall_repo.get_server(db, sid)
    assert uscall_repo.load_server_token(srv) == "tok-matriz"

    # token "" na edição também é rejeitado
    r = client.put(
        f"/api/config/uscall-servers/{sid}",
        json={"nome": "M", "host": "h", "token": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422


def test_delete_e_404(client, db) -> None:
    csrf = _authed(client, db)
    sid = _create(client, csrf).json()["id"]
    r = client.delete(
        f"/api/config/uscall-servers/{sid}", headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert client.get("/api/config").json()["uscall_servers"] == []
    r = client.delete(
        f"/api/config/uscall-servers/{sid}", headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


@respx.mock
def test_test_por_server_id_usa_credenciais_salvas(client, db) -> None:
    csrf = _authed(client, db)
    sid = _create(client, csrf).json()["id"]
    route = respx.get("https://pbx.matriz.com/api/extenstatus").mock(
        return_value=httpx.Response(200, json=[]),
    )
    r = client.post(
        "/api/uscall/test", json={"server_id": sid}, headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    # o token salvo (decriptado) foi usado na chamada
    assert route.calls.last.request.url.params["token"] == "tok-matriz"


def test_test_server_inexistente(client, db) -> None:
    csrf = _authed(client, db)
    r = client.post(
        "/api/uscall/test", json={"server_id": 999}, headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert r.json() == {
        "success": False, "http_status": None, "latency_ms": None,
        "error": "server_not_found",
    }
