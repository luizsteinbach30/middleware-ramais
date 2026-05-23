"""Endpoints novos de vinculação Device <-> ExtensionLine (Fase C/D)."""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.devices.repository import upsert_from_uscall
from middleware_monitor.domain.extension_configurator import (
    repository as ec_repo,
)


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


def _seed_unlinked(db) -> tuple[int, str]:
    """Cria device + linha sem vínculo. Retorna (device_id, line_id)."""
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    lines = ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="10.0.0.99", numero_ramal="3001"),  # IP diferente
    ])
    db.commit()
    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev = db.scalar(select(Device).where(Device.name == "3001"))
    assert dev is not None
    return dev.id, lines[0].id


def test_get_linked_line_retorna_null_sem_vinculo(client, db) -> None:
    _authed(client, db)
    device_id, _line_id = _seed_unlinked(db)
    r = client.get(f"/api/devices/{device_id}/extension-line")
    assert r.status_code == 200
    assert r.json() is None


def test_link_post_vincula_e_get_retorna(client, db) -> None:
    csrf = _authed(client, db)
    device_id, line_id = _seed_unlinked(db)
    r = client.post(
        f"/api/devices/{device_id}/link",
        json={"line_id": line_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["line_id"] == line_id
    assert body["environment_id"] == "filial"

    r2 = client.get(f"/api/devices/{device_id}/extension-line")
    assert r2.status_code == 200
    assert r2.json()["line_id"] == line_id


def test_link_post_rejeita_linha_inexistente(client, db) -> None:
    csrf = _authed(client, db)
    device_id, _ = _seed_unlinked(db)
    r = client.post(
        f"/api/devices/{device_id}/link",
        json={"line_id": "abcdef0123456789"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "line_not_found"


def test_link_post_rejeita_linha_ja_vinculada_a_outro(client, db) -> None:
    csrf = _authed(client, db)
    device_id, line_id = _seed_unlinked(db)
    # vincula no dev1
    client.post(
        f"/api/devices/{device_id}/link",
        json={"line_id": line_id},
        headers={"X-CSRF-Token": csrf},
    )
    # cria outro device
    from middleware_monitor.core.models import Device
    from sqlalchemy import select

    upsert_from_uscall(db, [
        {"ramal": "3002", "status": "disponivel", "ip": "10.0.0.51"},
    ])
    db.commit()
    other = db.scalar(select(Device).where(Device.name == "3002"))
    assert other is not None
    # tentar vincular a mesma linha no dev2
    r = client.post(
        f"/api/devices/{other.id}/link",
        json={"line_id": line_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409


def test_unlink_remove_vinculo(client, db) -> None:
    csrf = _authed(client, db)
    device_id, line_id = _seed_unlinked(db)
    client.post(
        f"/api/devices/{device_id}/link",
        json={"line_id": line_id},
        headers={"X-CSRF-Token": csrf},
    )
    r = client.delete(
        f"/api/devices/{device_id}/link",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    r2 = client.get(f"/api/devices/{device_id}/extension-line")
    assert r2.json() is None


def test_available_lines_lista_apenas_orfas(client, db) -> None:
    csrf = _authed(client, db)
    device_id, line_id = _seed_unlinked(db)
    # cria 2ª linha sem vínculo
    env = ec_repo.get_environment(db, "filial")
    assert env is not None
    ec_repo.save_lines(db, env, [
        {"id": line_id, "ip": "10.0.0.99", "numero_ramal": "3001"},
        ec_repo.new_line(ip="10.0.0.51", numero_ramal="3002"),
    ])
    db.commit()
    r = client.get(f"/api/devices/{device_id}/available-lines")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2

    # após vincular uma, ela some
    client.post(
        f"/api/devices/{device_id}/link",
        json={"line_id": line_id},
        headers={"X-CSRF-Token": csrf},
    )
    r2 = client.get(f"/api/devices/{device_id}/available-lines")
    assert len(r2.json()) == 1


def test_apply_config_falha_sem_vinculo(client, db) -> None:
    csrf = _authed(client, db)
    device_id, _ = _seed_unlinked(db)
    r = client.post(
        f"/api/devices/{device_id}/apply-config",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "device_not_linked"


def test_reapply_events_lista_vazia_para_device_sem_eventos(client, db) -> None:
    _authed(client, db)
    device_id, _ = _seed_unlinked(db)
    r = client.get(f"/api/devices/{device_id}/reapply-events")
    assert r.status_code == 200
    assert r.json() == []


def test_link_environments_lista_so_ambientes_com_orfas(client, db) -> None:
    _authed(client, db)
    device_id, _ = _seed_unlinked(db)
    # cria 2 ambientes: um com linha órfã, outro sem
    env_a = ec_repo.create_environment(db, nome="Vazio", modelo_telefone="HTEK UC902G")
    # nenhuma linha
    db.commit()
    r = client.get(f"/api/devices/{device_id}/link-environments")
    assert r.status_code == 200
    envs = r.json()
    nomes = [e["environment_nome"] for e in envs]
    # "Filial" (do _seed_unlinked) tem linha órfã, "Vazio" não
    assert "Filial" in nomes
    assert env_a.nome not in nomes  # "Vazio" não aparece


def test_link_environments_marca_has_match_quando_ip_bate(client, db) -> None:
    _authed(client, db)
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    env = ec_repo.create_environment(db, nome="Filial Match", modelo_telefone="HTEK UC902G")
    # primeira chamada de save_lines vai fazer auto-link! Pra forçar a linha
    # como órfã, criamos device DEPOIS de save_lines com IP que não bate.
    ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
    ])
    db.commit()
    # nesse ponto, a linha já está vinculada (auto-link em save_lines)
    # então pra testar has_match, criamos device + linha em outro ambiente sem vincular
    upsert_from_uscall(db, [
        {"ramal": "3002", "status": "disponivel", "ip": "10.0.0.99"},
    ])
    db.commit()
    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev2 = db.scalar(select(Device).where(Device.name == "3002"))
    assert dev2 is not None
    env2 = ec_repo.create_environment(db, nome="Outro", modelo_telefone="HTEK UC902G")
    # adicionamos linha sem IP igual ao dev2 (sem match) e linha com IP igual
    ec_repo.save_lines(db, env2, [
        ec_repo.new_line(ip="10.0.0.99", numero_ramal="3002"),
        ec_repo.new_line(ip="10.0.0.111", numero_ramal="9000"),
    ])
    db.commit()
    # MAS save_lines fez auto-link da 1ª — vamos desvincular pra forçar órfã
    ln = db.scalar(select(__import__("middleware_monitor.core.models", fromlist=["ExtensionLine"]).ExtensionLine).where(
        __import__("middleware_monitor.core.models", fromlist=["ExtensionLine"]).ExtensionLine.numero_ramal == "3002",
    ))
    assert ln is not None
    ec_repo.unlink_line_from_device(db, ln)
    db.commit()
    # consulta link-environments para dev2: deve trazer "Outro" com has_match=true
    r = client.get(f"/api/devices/{dev2.id}/link-environments")
    assert r.status_code == 200
    outro = next(e for e in r.json() if e["environment_id"] == env2.id)
    assert outro["has_match"] is True
    assert outro["orphan_lines"] >= 1


def test_link_suggestion_ip_match(client, db) -> None:
    _authed(client, db)
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    # cria linha que NÃO bate com nenhum device (pra ficar órfã após save_lines)
    ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
    ])
    db.commit()
    # auto-link já vinculou. Pra testar suggestion, desvinculamos
    from sqlalchemy import select

    from middleware_monitor.core.models import Device, ExtensionLine

    ln = db.scalar(select(ExtensionLine).where(ExtensionLine.numero_ramal == "3001"))
    assert ln is not None
    ec_repo.unlink_line_from_device(db, ln)
    db.commit()
    dev = db.scalar(select(Device).where(Device.name == "3001"))
    assert dev is not None
    r = client.get(
        f"/api/devices/{dev.id}/link-suggestion?environment_id={env.id}",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["auto_resolved"] is True
    assert body["reason"] == "ip_match"
    assert body["line"]["numero_ramal"] == "3001"


def test_link_suggestion_ambiguous_quando_varias_orfas_sem_match(
    client, db,
) -> None:
    _authed(client, db)
    upsert_from_uscall(db, [
        {"ramal": "9999", "status": "disponivel", "ip": "10.0.0.123"},
    ])
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
        ec_repo.new_line(ip="10.0.0.51", numero_ramal="3002"),
    ])
    db.commit()
    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev = db.scalar(select(Device).where(Device.name == "9999"))
    assert dev is not None
    r = client.get(
        f"/api/devices/{dev.id}/link-suggestion?environment_id={env.id}",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["auto_resolved"] is False
    assert body["reason"] == "ambiguous"
    assert len(body["candidates"]) == 2


def test_environment_detail_inclui_device_info_por_linha(client, db) -> None:
    _authed(client, db)
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
        ec_repo.new_line(ip="10.0.0.99", numero_ramal="3002"),
    ])
    db.commit()
    r = client.get(f"/api/extension-configurator/environments/{env.id}")
    assert r.status_code == 200
    body = r.json()
    linhas = {ln["numero_ramal"]: ln for ln in body["linhas"]}
    assert linhas["3001"]["device_id"] is not None
    assert linhas["3001"]["device_name"] == "3001"
    assert linhas["3002"]["device_id"] is None


def test_list_devices_inclui_info_de_vinculo(client, db) -> None:
    _authed(client, db)
    # cria linha → device (na ordem que dispara auto-link via upsert)
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="10.0.0.50", numero_ramal="3001", nome_visivel="Recepção"),
    ])
    db.commit()
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    db.commit()

    r = client.get("/api/devices")
    assert r.status_code == 200
    items = r.json()["items"]
    dev = next(d for d in items if d["name"] == "3001")
    assert dev["extension_environment_id"] == env.id
    assert dev["extension_environment_nome"] == "Filial"
    assert dev["extension_line_ramal"] == "3001"
    assert dev["extension_line_nome_visivel"] == "Recepção"


def test_detail_device_inclui_vinculo(client, db) -> None:
    _authed(client, db)
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
    ])
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    db.commit()
    from sqlalchemy import select

    from middleware_monitor.core.models import Device

    dev = db.scalar(select(Device).where(Device.name == "3001"))
    assert dev is not None
    r = client.get(f"/api/devices/{dev.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["extension_environment_id"] == env.id
    assert body["extension_line_ramal"] == "3001"


def test_auto_link_endpoint_vincula_e_e_idempotente(client, db) -> None:
    csrf = _authed(client, db)
    # device + linha com MESMO IP
    upsert_from_uscall(db, [
        {"ramal": "3001", "status": "disponivel", "ip": "10.0.0.50"},
    ])
    env = ec_repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    ec_repo.save_lines(db, env, [
        ec_repo.new_line(ip="10.0.0.50", numero_ramal="3001"),
    ])
    db.commit()
    # primeira chamada vincula  (na verdade upsert_from_uscall já faz auto-link,
    # mas como criamos a linha DEPOIS, esse upsert não pegou — endpoint vai
    # pegar agora)
    r = client.post(
        "/api/devices/auto-link", headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    # 2ª chamada — idempotente, 0 linkings
    r2 = client.post(
        "/api/devices/auto-link", headers={"X-CSRF-Token": csrf},
    )
    assert r2.status_code == 200
    assert r2.json()["linked"] == 0
