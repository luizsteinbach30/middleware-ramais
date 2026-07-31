"""API de device actions: capabilities, ação por linha, normalize, guard do set_ip."""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def _env_with_line(client, csrf, modelo: str, ip: str = "10.0.0.10") -> str:
    r = client.post(
        "/api/extension-configurator/environments",
        json={"nome": "Loja", "modelo_telefone": modelo},
        headers={"X-CSRF-Token": csrf},
    )
    env_id = r.json()["id"]
    client.put(
        f"/api/extension-configurator/environments/{env_id}/lines",
        json={"linhas": [{"ip": ip, "numero_ramal": "3660"}]},
        headers={"X-CSRF-Token": csrf},
    )
    return env_id


def test_capabilities_por_vendor(client, db) -> None:
    csrf = _authed(client, db)
    env_y = _env_with_line(client, csrf, "Yealink T31G")
    caps = client.get(f"/api/extension-configurator/environments/{env_y}/capabilities").json()
    assert "normalize" in caps["actions"]

    # V-series Intelbras ainda não homologado → sem ações
    env_i = _env_with_line(client, csrf, "Intelbras V5501", ip="10.0.0.11")
    caps_i = client.get(f"/api/extension-configurator/environments/{env_i}/capabilities").json()
    assert caps_i["actions"] == []


def test_action_requer_csrf(client, db) -> None:
    csrf = _authed(client, db)
    env = _env_with_line(client, csrf, "Yealink T31G")
    line_id = client.get(
        f"/api/extension-configurator/environments/{env}"
    ).json()["linhas"][0]["id"]
    r = client.post(
        f"/api/extension-configurator/environments/{env}/lines/{line_id}/actions/normalize",
    )
    assert r.status_code == 403


def test_set_ip_exige_confirmacao_do_ip_atual(client, db) -> None:
    csrf = _authed(client, db)
    env = _env_with_line(client, csrf, "Yealink T31G", ip="10.0.0.20")
    line_id = client.get(
        f"/api/extension-configurator/environments/{env}"
    ).json()["linhas"][0]["id"]
    # confirm_ip errado → 400
    r = client.post(
        f"/api/extension-configurator/environments/{env}/lines/{line_id}/actions/set_ip",
        json={"params": {"new_ip": "10.0.0.99"}, "confirm_ip": "9.9.9.9"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "confirm_ip_mismatch"


def test_normalize_em_massa_registra_auditoria(client, db, monkeypatch) -> None:
    csrf = _authed(client, db)
    env = _env_with_line(client, csrf, "Yealink T31G", ip="10.0.0.30")

    # não bate no aparelho real: mocka a execução do adapter
    from middleware_monitor.integrations.extension_configurator.vendors.base import (
        ActionResult,
    )
    from middleware_monitor.integrations.extension_configurator.vendors.yealink import (
        YealinkAdapter,
    )

    async def _fake_exec(self, ip, creds, action, params):
        return ActionResult(ok=True, detail="ok")

    monkeypatch.setattr(YealinkAdapter, "execute_action", _fake_exec)

    r = client.post(
        f"/api/extension-configurator/environments/{env}/actions/normalize",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["total"] == 1 and body["ok"] == 1

    # auditoria gravada
    events = client.get(
        f"/api/extension-configurator/environments/{env}/action-events"
    ).json()["events"]
    assert len(events) == 1
    assert events[0]["action"] == "normalize"
    assert events[0]["status"] == "ok"
    assert events[0]["vendor"] == "yealink"
