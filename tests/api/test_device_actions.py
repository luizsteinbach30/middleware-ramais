"""API de device actions: capabilities, ação por linha, normalize, guard do set_ip."""

from __future__ import annotations

import time

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
    assert body["total"] == 1
    run_id = body["run_id"]

    # roda em background: acompanha pelo endpoint live até terminar
    deadline = time.time() + 5
    while True:
        live = client.get(f"/api/extension-configurator/action-runs/{run_id}/live")
        assert live.status_code == 200, live.json()
        data = live.json()
        if data["finished_at"] is not None:
            break
        assert time.time() < deadline, "normalize não terminou a tempo"
        time.sleep(0.05)

    assert data["action"] == "normalize"
    assert data["summary"]["done"] == 1
    assert data["rows"][0]["stage"] == "done"

    # auditoria gravada
    events = client.get(
        f"/api/extension-configurator/environments/{env}/action-events"
    ).json()["events"]
    assert len(events) == 1
    assert events[0]["action"] == "normalize"
    assert events[0]["status"] == "ok"
    assert events[0]["vendor"] == "yealink"


def test_normalize_respeita_selected_ids(client, db, monkeypatch) -> None:
    csrf = _authed(client, db)
    env = _env_with_line(client, csrf, "Yealink T31G", ip="10.0.0.40")
    # segunda linha no mesmo ambiente
    client.put(
        f"/api/extension-configurator/environments/{env}/lines",
        json={"linhas": [
            {"ip": "10.0.0.40", "numero_ramal": "3660"},
            {"ip": "10.0.0.41", "numero_ramal": "3661"},
        ]},
        headers={"X-CSRF-Token": csrf},
    )
    linhas = client.get(f"/api/extension-configurator/environments/{env}").json()["linhas"]
    assert len(linhas) == 2

    from middleware_monitor.integrations.extension_configurator.vendors.base import (
        ActionResult,
    )
    from middleware_monitor.integrations.extension_configurator.vendors.yealink import (
        YealinkAdapter,
    )

    async def _fake_exec(self, ip, creds, action, params):
        return ActionResult(ok=True, detail="ok")

    monkeypatch.setattr(YealinkAdapter, "execute_action", _fake_exec)

    # só a 2ª linha selecionada → total == 1
    alvo = next(ln for ln in linhas if ln["ip"] == "10.0.0.41")
    r = client.post(
        f"/api/extension-configurator/environments/{env}/actions/normalize",
        json={"selected_ids": [alvo["id"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["total"] == 1

    # sem body → todas com IP
    r2 = client.post(
        f"/api/extension-configurator/environments/{env}/actions/normalize",
        headers={"X-CSRF-Token": csrf},
    )
    assert r2.status_code == 200, r2.json()
    assert r2.json()["total"] == 2


def test_action_run_live_404_para_run_desconhecido(client, db) -> None:
    _authed(client, db)
    r = client.get("/api/extension-configurator/action-runs/nao-existe/live")
    assert r.status_code == 404
