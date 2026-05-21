"""Endpoints do Configurador de Ramais: auth, CSRF, CRUD basico."""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post(
        "/api/auth/login",
        json={"username": user.username, "password": plaintext},
    )
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def test_list_environments_requer_auth(client) -> None:
    r = client.get("/api/extension-configurator/environments")
    assert r.status_code == 401


def test_create_environment_requer_csrf(client, db) -> None:
    _authed(client, db)
    r = client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Unidade 03", "modelo_telefone": "HTEK UC902G"},
    )
    assert r.status_code == 403


def test_create_and_list_environment_fluxo_completo(client, db) -> None:
    csrf = _authed(client, db)
    # cria
    r = client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Unidade 03", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["id"] == "unidade-03"
    assert body["telefones"] == 0
    # lista
    r = client.get("/api/extension-configurator/environments")
    assert r.status_code == 200
    envs = r.json()["environments"]
    assert any(e["id"] == "unidade-03" for e in envs)


def test_environment_detail_traz_config_padrao_e_status(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "Intelbras V5501"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.get("/api/extension-configurator/environments/lab")
    assert r.status_code == 200
    body = r.json()
    assert body["modelo_telefone"] == "Intelbras V5501"
    assert body["config_padrao"]["keylock_enable"] == 2
    assert body["config_padrao"]["keylock_timeout"] == 30
    assert body["linhas"] == []


def test_save_lines_e_calcula_status(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.put(
        "/api/extension-configurator/environments/lab/lines",
        json={"linhas": [
            {"ip": "192.168.0.10", "numero_ramal": "3660", "senha_sip": "s1"},
            {"ip": "", "numero_ramal": "3661"},
        ]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert len(body["linhas"]) == 2
    # Linha com IP: status calculado (pending pois nunca foi aplicada)
    com_ip = next(ln for ln in body["linhas"] if ln["ip"])
    assert com_ip["status"] == "pending"
    assert len(com_ip["hash_atual"]) == 64


def test_update_environment_atualiza_config_padrao(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.put(
        "/api/extension-configurator/environments/lab",
        json={"config_padrao": {"sip_server": "pbx.novo.com"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    detail = client.get("/api/extension-configurator/environments/lab").json()
    assert detail["config_padrao"]["sip_server"] == "pbx.novo.com"
    # defaults preservados
    assert detail["config_padrao"]["keylock_enable"] == 2


def test_delete_environment(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.delete(
        "/api/extension-configurator/environments/lab",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert client.get(
        "/api/extension-configurator/environments/lab",
    ).status_code == 404


def test_phone_models_exposto(client, db) -> None:
    _authed(client, db)
    r = client.get("/api/extension-configurator/phone-models")
    assert r.status_code == 200
    models = r.json()["models"]
    assert "HTEK UC902G" in models
    assert any("Intelbras" in m for m in models)


def test_apply_environment_sem_linhas_devolve_total_zero(client, db) -> None:
    csrf = _authed(client, db)
    client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Lab", "modelo_telefone": "HTEK UC902G"},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.post(
        "/api/extension-configurator/environments/lab/apply",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["total"] == 0
    assert len(body["run_id"]) == 12
