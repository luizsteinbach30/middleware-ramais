"""Unit tests do repository do Configurador de Ramais."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session as DBSession

from middleware_monitor.domain.extension_configurator import (
    defaults,
)
from middleware_monitor.domain.extension_configurator import (
    repository as repo,
)


def test_generate_slug_normaliza_acentos_e_espacos() -> None:
    assert repo.generate_slug("Unidade 03") == "unidade-03"
    assert repo.generate_slug("Ambiência São Paulo!") == "ambiencia-sao-paulo"
    assert repo.generate_slug("   ") .startswith("ambiente-")


def test_create_environment_persiste_com_defaults(db: DBSession) -> None:
    env = repo.create_environment(db, nome="Unidade 03", modelo_telefone="Intelbras V5501")
    db.commit()
    assert env.id == "unidade-03"
    assert env.nome == "Unidade 03"
    assert env.modelo_telefone == "Intelbras V5501"
    saved = json.loads(env.config_padrao)
    assert saved["keylock_enable"] == 2
    assert saved["keylock_timeout"] == 30
    assert saved["validar_conectividade"] is True
    assert saved["function_keys"][0]["label"] == "Central"


def test_create_environment_evita_colisao_de_slug(db: DBSession) -> None:
    a = repo.create_environment(db, nome="Unidade 03", modelo_telefone="HTEK UC902G")
    b = repo.create_environment(db, nome="Unidade 03", modelo_telefone="HTEK UC902G")
    db.commit()
    assert a.id == "unidade-03"
    assert b.id == "unidade-03-2"


def test_merged_config_padrao_aplica_defaults_para_chaves_novas(db: DBSession) -> None:
    env = repo.create_environment(db, nome="Lab", modelo_telefone="HTEK UC902G")
    env.config_padrao = json.dumps({"sip_server": "pbx.legacy.com"})  # JSON antigo
    db.flush()
    merged = repo.merged_config_padrao(env)
    assert merged["sip_server"] == "pbx.legacy.com"  # preservado
    assert merged["keylock_enable"] == 2              # default novo aplicado
    assert "function_keys" in merged


def test_update_environment_merge_preserva_outras_chaves(db: DBSession) -> None:
    env = repo.create_environment(db, nome="Lab", modelo_telefone="HTEK UC902G")
    repo.update_environment(db, env, config_padrao={"sip_server": "pbx.novo.com"})
    db.commit()
    merged = repo.merged_config_padrao(env)
    assert merged["sip_server"] == "pbx.novo.com"
    assert merged["keylock_enable"] == 2  # default mantido


def test_save_lines_upsert_remove_ausentes_preserva_historico(db: DBSession) -> None:
    env = repo.create_environment(db, nome="Lab", modelo_telefone="HTEK UC902G")
    rows = [
        repo.new_line(ip="192.168.0.10", numero_ramal="3660"),
        repo.new_line(ip="192.168.0.11", numero_ramal="3661"),
    ]
    saved = repo.save_lines(db, env, rows)
    db.commit()
    assert len(saved) == 2
    # Simula histórico em uma das linhas
    repo.update_line_status(db, saved[0], status="ok", hash_aplicado="abc123")
    db.commit()
    # Re-save mantendo só a primeira linha (segunda some)
    repo.save_lines(
        db, env,
        [{"id": saved[0].id, "ip": "192.168.0.10", "numero_ramal": "3660"}],
    )
    db.commit()
    after = repo.list_lines(db, env.id)
    assert len(after) == 1
    assert after[0].id == saved[0].id
    assert after[0].ultimo_status == "ok"          # histórico preservado
    assert after[0].ultimo_hash_aplicado == "abc123"


def test_save_lines_aceita_linha_sem_id_e_gera_uuid(db: DBSession) -> None:
    env = repo.create_environment(db, nome="Lab", modelo_telefone="HTEK UC902G")
    saved = repo.save_lines(db, env, [{"ip": "192.168.0.99", "numero_ramal": "3699"}])
    db.commit()
    assert len(saved) == 1
    assert len(saved[0].id) == 32  # uuid.hex
    assert saved[0].numero_ramal == "3699"


def test_delete_environment_cascata_em_linhas_e_runs(db: DBSession) -> None:
    env = repo.create_environment(db, nome="Lab", modelo_telefone="HTEK UC902G")
    repo.save_lines(db, env, [repo.new_line(ip="192.168.0.10", numero_ramal="3660")])
    repo.create_run(db, env.id, total=1, forcado=False, operador="tester")
    db.commit()
    assert repo.delete_environment(db, env.id) is True
    db.commit()
    assert repo.get_environment(db, env.id) is None
    assert repo.list_runs(db, env_id=env.id) == []


def test_create_run_e_finish_run_atualiza_metricas(db: DBSession) -> None:
    env = repo.create_environment(db, nome="Lab", modelo_telefone="HTEK UC902G")
    run = repo.create_run(db, env.id, total=10, forcado=True, operador="admin")
    db.commit()
    assert run.id is not None
    assert run.total == 10
    assert run.forcado is True
    assert run.finished_at is None
    repo.finish_run(db, run, ok=8, falha=2)
    db.commit()
    assert run.finished_at is not None
    assert run.ok == 8 and run.falha == 2


def test_list_environments_ordenado_por_nome(db: DBSession) -> None:
    repo.create_environment(db, nome="Zulu", modelo_telefone="HTEK UC902G")
    repo.create_environment(db, nome="Alpha", modelo_telefone="HTEK UC902G")
    db.commit()
    envs = repo.list_environments(db)
    assert [e.nome for e in envs] == ["Alpha", "Zulu"]


def test_default_config_padrao_inclui_todas_as_chaves_documentadas() -> None:
    cfg = defaults.default_config_padrao()
    for k in (
        "sip_server", "web_user", "web_password", "nova_web_user", "nova_web_password",
        "ntp_server", "timezone", "validar_conectividade",
        "menu_password", "keylock_password", "keylock_enable", "keylock_timeout",
        "function_keys",
    ):
        assert k in cfg, f"chave default ausente: {k}"
    assert cfg["keylock_enable"] == 2
    assert cfg["keylock_timeout"] == 30
