"""Relatório de execução: snapshot por linha (status antes/depois) e o
endpoint /runs/{id}/detail com snapshot + fallback para runs antigos."""

from __future__ import annotations

from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.extension_configurator import repository as repo


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    assert client.post(
        "/api/auth/login", json={"username": user.username, "password": plaintext},
    ).status_code == 200
    return client.cookies.get("mm_csrf") or ""


def test_run_line_crud(db) -> None:
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    run = repo.create_run(db, env.id, total=1, forcado=False, operador="admin")
    db.commit()
    rl = repo.create_run_line(
        db, run_id=run.id, line_id="abc", numero_ramal="3001",
        ip="10.0.0.1", nome_visivel="Recepção", status_antes="pending",
    )
    db.commit()
    assert rl.status_depois == "running"
    repo.finish_run_line(db, rl, status_depois="ok", modelo="UC902G")
    db.commit()
    rows = repo.list_run_lines(db, run.id)
    assert len(rows) == 1
    assert rows[0].status_antes == "pending"
    assert rows[0].status_depois == "ok"
    assert rows[0].modelo == "UC902G"


def test_run_detail_com_snapshot(client, db) -> None:
    _authed(client, db)
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    run = repo.create_run(db, env.id, total=2, forcado=False, operador="joao")
    repo.create_run_line(
        db, run_id=run.id, line_id="l1", numero_ramal="3001",
        ip="10.0.0.1", nome_visivel="", status_antes="pending",
    )
    rl2 = repo.create_run_line(
        db, run_id=run.id, line_id="l2", numero_ramal="3002",
        ip="10.0.0.2", nome_visivel="", status_antes="outdated",
    )
    repo.finish_run_line(db, rl2, status_depois="erro", erro="login recusado")
    repo.finish_run(db, run, ok=1, falha=1)
    db.commit()

    r = client.get(f"/api/extension-configurator/runs/{run.id}/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["tem_snapshot"] is True
    assert body["run"]["operador"] == "joao"
    by_ramal = {i["numero_ramal"]: i for i in body["impactadas"]}
    assert set(by_ramal) == {"3001", "3002"}
    assert by_ramal["3002"]["status_antes"] == "outdated"
    assert by_ramal["3002"]["status_depois"] == "erro"
    assert by_ramal["3002"]["erro"] == "login recusado"


def test_run_detail_fallback_sem_snapshot(client, db) -> None:
    _authed(client, db)
    env = repo.create_environment(db, nome="Filial", modelo_telefone="HTEK UC902G")
    repo.save_lines(db, env, [repo.new_line(ip="10.0.0.1", numero_ramal="3001")])
    run = repo.create_run(db, env.id, total=0, forcado=False, operador="admin")
    db.commit()

    r = client.get(f"/api/extension-configurator/runs/{run.id}/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["tem_snapshot"] is False
    assert "linhas" in body
