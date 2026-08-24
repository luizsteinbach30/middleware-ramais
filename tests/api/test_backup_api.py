"""API de backup: exportar, importar, snapshot e agendamento.

Cobre tambem o que a tela NAO pode deixar acontecer: exportar sem passphrase,
baixar arquivo de fora da pasta de backups e importar pacote com a passphrase
errada.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.backup import snapshot as snap
from middleware_monitor.domain.extension_configurator import repository as ec_repo


def _authed(client, db: Session) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def _com_alembic(db: Session) -> None:
    revisao = sorted(snap.known_revisions())[0]
    db.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db.execute(text("DELETE FROM alembic_version"))
    db.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": revisao})
    db.commit()


def test_exportar_e_importar_ambiente_de_volta(client, db: Session) -> None:
    csrf = _authed(client, db)
    env = ec_repo.create_environment(db, nome="Loja 14", modelo_telefone="Intelbras S3002")
    ec_repo.save_lines(db, env, [{"ip": "192.168.0.48", "numero_ramal": "1401"}])
    db.commit()

    r = client.post(
        "/api/backup/export",
        json={"passphrase": "frase-forte", "sections": ["environments"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert ".mwrbak" in r.headers["content-disposition"]
    blob = r.text

    # inspecionar antes de aplicar (é o que a tela mostra)
    r = client.post(
        "/api/backup/inspect",
        json={"blob": blob, "passphrase": "frase-forte"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["sections"]["environments"]["ambientes"] == 1

    ec_repo.delete_environment(db, env.id)
    db.commit()

    r = client.post(
        "/api/backup/import",
        json={"blob": blob, "passphrase": "frase-forte", "mode": "replace"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["applied"]["environments"]["novos"] == 1
    assert [e.nome for e in ec_repo.list_environments(db)] == ["Loja 14"]


def test_exportar_exige_passphrase(client, db: Session) -> None:
    csrf = _authed(client, db)
    r = client.post(
        "/api/backup/export", json={"passphrase": ""}, headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422


def test_importar_com_passphrase_errada_nao_aplica(client, db: Session) -> None:
    csrf = _authed(client, db)
    blob = client.post(
        "/api/backup/export",
        json={"passphrase": "certa"},
        headers={"X-CSRF-Token": csrf},
    ).text
    r = client.post(
        "/api/backup/import",
        json={"blob": blob, "passphrase": "errada"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "passphrase" in r.json()["detail"]


def test_snapshot_manual_aparece_na_listagem_e_baixa(client, db: Session) -> None:
    csrf = _authed(client, db)
    _com_alembic(db)

    r = client.post("/api/backup/snapshot", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.json()
    nome = r.json()["name"]

    listagem = client.get("/api/backup/files").json()
    assert nome in [f["name"] for f in listagem["files"]]
    assert listagem["total_bytes"] > 0

    baixado = client.get(f"/api/backup/files/{nome}")
    assert baixado.status_code == 200
    assert baixado.content[:2] == b"\x1f\x8b"  # gzip


def test_download_nao_escapa_da_pasta_de_backups(client, db: Session) -> None:
    _authed(client, db)
    r = client.get("/api/backup/files/..%2F..%2Fdb%2Fapp.db")
    assert r.status_code == 404


def test_restaurar_agenda_e_pode_ser_cancelado(client, db: Session) -> None:
    csrf = _authed(client, db)
    _com_alembic(db)
    nome = client.post("/api/backup/snapshot", headers={"X-CSRF-Token": csrf}).json()["name"]

    r = client.post(
        "/api/backup/restore", json={"name": nome}, headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    assert client.get("/api/backup/restore").json()["pending"]["source"] == nome

    r = client.request("DELETE", "/api/backup/restore", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert client.get("/api/backup/restore").json()["pending"] is None


def test_restaurar_arquivo_inexistente(client, db: Session) -> None:
    csrf = _authed(client, db)
    r = client.post(
        "/api/backup/restore", json={"name": "nao-existe.db.gz"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_configuracao_do_backup_automatico(client, db: Session) -> None:
    csrf = _authed(client, db)
    cfg = client.get("/api/backup/settings").json()
    assert cfg["auto_enabled"] is True
    assert (cfg["hour"], cfg["minute"]) == (2, 30)
    assert cfg["has_passphrase"] is False

    r = client.put(
        "/api/backup/settings",
        json={"hour": 4, "minute": 15, "keep": 3, "export_passphrase": "frase"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    salvo = r.json()
    assert (salvo["hour"], salvo["minute"], salvo["keep"]) == (4, 15, 3)
    assert salvo["has_passphrase"] is True
    # a passphrase salva nunca volta pela API
    assert "export_passphrase" not in salvo
    assert client.get("/api/backup/settings").json()["hour"] == 4


def test_apagar_backup(client, db: Session) -> None:
    csrf = _authed(client, db)
    _com_alembic(db)
    nome = client.post("/api/backup/snapshot", headers={"X-CSRF-Token": csrf}).json()["name"]

    r = client.request(
        "DELETE", f"/api/backup/files/{nome}", headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert client.get("/api/backup/files").json()["files"] == []


def test_sem_sessao_nao_ha_backup(client, db: Session) -> None:
    assert client.get("/api/backup/files").status_code == 401
    assert client.post("/api/backup/snapshot").status_code in (401, 403)


def test_fluxo_de_conflito_pela_api(client, db: Session) -> None:
    """Exportar, mudar o ambiente no sistema, comparar e escolher qual fica."""
    csrf = _authed(client, db)
    env = ec_repo.create_environment(db, nome="Loja 14", modelo_telefone="Intelbras S3002")
    ec_repo.save_lines(db, env, [{"ip": "192.168.0.48", "numero_ramal": "1401"}])
    db.commit()
    blob = client.post(
        "/api/backup/export",
        json={"passphrase": "frase", "sections": ["environments"]},
        headers={"X-CSRF-Token": csrf},
    ).text

    # o sistema muda depois do export
    ec_repo.update_environment(db, env, nome="Loja 14 (reformada)")
    db.commit()

    r = client.post(
        "/api/backup/diff",
        json={"blob": blob, "passphrase": "frase", "sections": ["environments"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    grupo = r.json()["groups"]["environments"]
    assert grupo["conflitos_total"] == 1
    conflito = grupo["conflitos"][0]
    assert conflito["key"] == f"environments:{env.id}"
    campos = {c["campo"]: c for c in conflito["campos"]}
    assert campos["nome"]["atual"] == "Loja 14 (reformada)"
    assert campos["nome"]["arquivo"] == "Loja 14"

    # decidindo pelo que está no sistema, o import não desfaz a mudança
    r = client.post(
        "/api/backup/import",
        json={
            "blob": blob, "passphrase": "frase", "sections": ["environments"],
            "decisions": {conflito["key"]: "atual"},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["applied"]["environments"]["mantidos"] == 1
    assert ec_repo.list_environments(db)[0].nome == "Loja 14 (reformada)"


def test_importar_pacote_igual_ao_sistema_nao_muda_nada(client, db: Session) -> None:
    csrf = _authed(client, db)
    ec_repo.create_environment(db, nome="Loja 14", modelo_telefone="Intelbras S3002")
    db.commit()
    blob = client.post(
        "/api/backup/export", json={"passphrase": "frase"},
        headers={"X-CSRF-Token": csrf},
    ).text

    grupos = client.post(
        "/api/backup/diff", json={"blob": blob, "passphrase": "frase"},
        headers={"X-CSRF-Token": csrf},
    ).json()["groups"]
    assert grupos["environments"]["conflitos_total"] == 0
    assert grupos["environments"]["identicos"] == 1

    aplicado = client.post(
        "/api/backup/import", json={"blob": blob, "passphrase": "frase"},
        headers={"X-CSRF-Token": csrf},
    ).json()["applied"]
    assert all(g["atualizados"] == 0 and g["novos"] == 0 for g in aplicado.values())


def test_decisao_invalida_e_recusada_pela_api(client, db: Session) -> None:
    csrf = _authed(client, db)
    blob = client.post(
        "/api/backup/export", json={"passphrase": "frase"},
        headers={"X-CSRF-Token": csrf},
    ).text
    r = client.post(
        "/api/backup/import",
        json={"blob": blob, "passphrase": "frase", "decisions": {"users:admin": "talvez"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_exportar_apenas_os_ambientes_selecionados(client, db: Session) -> None:
    """A tela de Ambientes exporta o que está marcado, não o sistema inteiro."""
    csrf = _authed(client, db)
    a = ec_repo.create_environment(db, nome="Loja 14", modelo_telefone="Intelbras S3002")
    ec_repo.create_environment(db, nome="Loja 20", modelo_telefone="HTEK UC912")
    db.commit()

    blob = client.post(
        "/api/backup/export",
        json={"passphrase": "frase", "sections": ["environments"], "environment_ids": [a.id]},
        headers={"X-CSRF-Token": csrf},
    ).text
    resumo = client.post(
        "/api/backup/inspect", json={"blob": blob, "passphrase": "frase"},
        headers={"X-CSRF-Token": csrf},
    ).json()

    assert resumo["sections"]["environments"] == {
        "ambientes": 1, "linhas": 0, "nomes": ["Loja 14"],
    }


def test_selecao_vazia_nao_vira_exportar_tudo(client, db: Session) -> None:
    """Seleção perdida no caminho não pode virar export do sistema inteiro."""
    csrf = _authed(client, db)
    ec_repo.create_environment(db, nome="Loja 14", modelo_telefone="Intelbras S3002")
    db.commit()

    r = client.post(
        "/api/backup/export",
        json={"passphrase": "frase", "environment_ids": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_exportar_ambiente_inexistente(client, db: Session) -> None:
    csrf = _authed(client, db)
    r = client.post(
        "/api/backup/export",
        json={"passphrase": "frase", "sections": ["environments"],
              "environment_ids": ["nao-existe"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404
