"""Endpoints de ações em massa sobre devices: delete, add-to-environment,
create-environment, e os novos filtros da listagem."""

from __future__ import annotations

from sqlalchemy import select

from middleware_monitor.core.models import Device
from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.devices.repository import upsert_from_uscall
from middleware_monitor.domain.extension_configurator import repository as ec_repo


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


def _seed_devices(db) -> dict[str, int]:
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.10"},
        {"ramal": "3002", "status": "disponivel", "ip": "10.0.0.11"},
        {"ramal": "3003", "status": "disponivel", "ip": "10.0.0.12"},
    ])
    db.commit()
    devs = db.scalars(select(Device)).all()
    return {d.name: d.id for d in devs}


def test_bulk_delete_remove_selecionados(client, db) -> None:
    csrf = _authed(client, db)
    ids = _seed_devices(db)
    r = client.post(
        "/api/devices/bulk/delete",
        json={"device_ids": [ids["3001"], ids["3002"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["deleted"] == 2

    remaining = {d.name for d in db.scalars(select(Device)).all()}
    assert remaining == {"3003"}


def test_bulk_delete_exige_csrf(client, db) -> None:
    _authed(client, db)
    ids = _seed_devices(db)
    r = client.post(
        "/api/devices/bulk/delete",
        json={"device_ids": [ids["3001"]]},
    )
    assert r.status_code == 403


def test_create_environment_from_devices(client, db) -> None:
    csrf = _authed(client, db)
    ids = _seed_devices(db)
    r = client.post(
        "/api/devices/bulk/create-environment",
        json={
            "nome": "Loja Centro",
            "modelo_telefone": "HTEK UC912",
            "device_ids": [ids["3001"], ids["3002"]],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["added"] == 2
    assert body["skipped"] == 0
    env_id = body["environment_id"]

    lines = ec_repo.list_lines(db, env_id)
    assert {ln.numero_ramal for ln in lines} == {"3001", "3002"}
    assert all(ln.device_id is not None for ln in lines)


def test_add_to_environment_pula_duplicados(client, db) -> None:
    csrf = _authed(client, db)
    ids = _seed_devices(db)
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    db.commit()

    r1 = client.post(
        "/api/devices/bulk/add-to-environment",
        json={"environment_id": env.id, "device_ids": [ids["3001"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r1.status_code == 200
    assert r1.json()["added"] == 1

    # segunda chamada com o mesmo device → já vinculado, pula
    r2 = client.post(
        "/api/devices/bulk/add-to-environment",
        json={"environment_id": env.id, "device_ids": [ids["3001"], ids["3002"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["added"] == 1  # só o 3002
    assert body["skipped"] == 1  # 3001 já vinculado


def test_add_to_environment_404_ambiente_inexistente(client, db) -> None:
    csrf = _authed(client, db)
    ids = _seed_devices(db)
    r = client.post(
        "/api/devices/bulk/add-to-environment",
        json={"environment_id": "nao-existe", "device_ids": [ids["3001"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404


def test_list_filtros_ip_e_ramal_via_api(client, db) -> None:
    _authed(client, db)
    _seed_devices(db)
    r = client.get("/api/devices?ip_from=10.0.0.10&ip_to=10.0.0.11")
    assert r.status_code == 200
    names = {d["name"] for d in r.json()["items"]}
    assert names == {"3001", "3002"}

    r2 = client.get("/api/devices?ramal_from=3003")
    names2 = {d["name"] for d in r2.json()["items"]}
    assert names2 == {"3003"}
