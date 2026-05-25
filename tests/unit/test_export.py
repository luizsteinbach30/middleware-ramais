"""Exportação de ambientes (XLSX/PDF) — montagem dos dados e geração binária."""

from __future__ import annotations

from middleware_monitor.domain.extension_configurator import export
from middleware_monitor.domain.extension_configurator import repository as repo


def _env_com_linha(db):
    env = repo.create_environment(db, nome="Loja Centro", modelo_telefone="HTEK UC902G")
    repo.update_environment(db, env, config_padrao={
        "sip_server": "10.0.0.1",
        "web_password": "segredo",
        "nova_web_password": "nova123",
    })
    repo.save_lines(db, env, [
        repo.new_line(ip="10.0.0.50", numero_ramal="3001", nome_visivel="Recepção",
                      senha_sip="x", servidor_sip="10.0.0.1"),
    ])
    db.commit()
    return env


def test_build_reports_mascara_senhas_e_lista_linhas(db) -> None:
    env = _env_com_linha(db)
    reports = export.build_reports(db, [env.id])
    assert len(reports) == 1
    r = reports[0]
    assert r.modelo_telefone == "HTEK UC902G"
    cfg = dict(r.config)
    assert cfg["Servidor SIP"] == "10.0.0.1"
    assert cfg["Senha web (atual)"] == "****"   # mascarado
    assert cfg["Nova senha web"] == "****"
    assert len(r.linhas) == 1
    assert r.linhas[0].ramal == "3001"
    assert r.linhas[0].ip == "10.0.0.50"
    assert r.linhas[0].nome_visivel == "Recepção"


def test_build_reports_ignora_ids_inexistentes(db) -> None:
    env = _env_com_linha(db)
    reports = export.build_reports(db, [env.id, "nao-existe"])
    assert [r.id for r in reports] == [env.id]


def test_to_xlsx_gera_arquivo_valido(db) -> None:
    env = _env_com_linha(db)
    reports = export.build_reports(db, [env.id])
    data = export.to_xlsx(reports)
    assert data[:2] == b"PK"  # zip/xlsx magic


def test_to_pdf_gera_arquivo_valido(db) -> None:
    env = _env_com_linha(db)
    reports = export.build_reports(db, [env.id])
    data = export.to_pdf(reports)
    assert data[:4] == b"%PDF"


def test_export_vazio_ainda_gera_arquivos() -> None:
    assert export.to_xlsx([])[:2] == b"PK"
    assert export.to_pdf([])[:4] == b"%PDF"


def test_export_endpoint_xlsx_e_pdf(client, db) -> None:
    from middleware_monitor.domain.auth.service import bootstrap_admin

    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    assert client.post(
        "/api/auth/login", json={"username": user.username, "password": plaintext},
    ).status_code == 200
    env = _env_com_linha(db)

    r = client.get(f"/api/extension-configurator/export?format=xlsx&ids={env.id}")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"

    r2 = client.get(f"/api/extension-configurator/export?format=pdf&ids={env.id}")
    assert r2.status_code == 200
    assert r2.headers["content-type"] == "application/pdf"
    assert r2.content[:4] == b"%PDF"

    r3 = client.get("/api/extension-configurator/export?format=zip&ids=" + env.id)
    assert r3.status_code == 400
