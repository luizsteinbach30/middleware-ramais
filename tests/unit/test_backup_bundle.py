"""Pacote portavel de backup: o que ele leva e o que acontece ao aplicar.

O ponto que mais importa aqui e a **portabilidade dos segredos**: o pacote sai
com o token/senha em claro dentro do envelope cifrado por passphrase, porque a
cifra local deriva do ``APP_SECRET_KEY`` da maquina. O teste que troca a chave
entre exportar e importar e o que prova isso.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from middleware_monitor.core.crypto import SecretBox
from middleware_monitor.core.export_crypto import decrypt_export, encrypt_export
from middleware_monitor.core.models import AppConfig, Device, User
from middleware_monitor.domain.backup import bundle as bundle_mod
from middleware_monitor.domain.backup.settings import KEY_PASSPHRASE, save_backup_settings
from middleware_monitor.domain.extension_configurator import repository as ec_repo
from middleware_monitor.domain.mqtt import repository as mqtt_repo
from middleware_monitor.domain.uscall import repository as uscall_repo
from middleware_monitor.settings import get_settings


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _povoar(db: Session) -> None:
    db.add(AppConfig(
        key="ping_timeout_ms", value="1500", is_secret=False, updated_at=_agora(),
    ))
    db.add(AppConfig(
        key="webhooks.devices.token",
        value=SecretBox(get_settings().secret_key).encrypt("segredo-do-webhook"),
        is_secret=True,
        updated_at=_agora(),
    ))
    uscall_repo.create_server(
        db, nome="PBX Matriz", host="https://pbx.exemplo", token_plain="tok-123",
    )
    mqtt_repo.create_broker(
        db, nome="EMQX", address_input="emqx.exemplo", host="emqx.exemplo",
        port=8883, tls=True, username="coletor", password_plain="senha-broker",
        topics=["ramais/#"],
    )
    env = ec_repo.create_environment(db, nome="Loja 14", modelo_telefone="Intelbras S3002")
    ec_repo.save_lines(db, env, [
        {"ip": "192.168.0.48", "numero_ramal": "1401", "senha_sip": "sip-secreta",
         "nome_visivel": "Balcao"},
        {"ip": "192.168.0.49", "numero_ramal": "1402", "senha_sip": "outra",
         "nome_visivel": "Caixa"},
    ])
    db.add(User(
        username="operador", password_hash="hash-fake", role="operator",
        created_at=_agora(),
    ))
    db.add(Device(
        name="1401", ip="192.168.0.48", mac="00:11:22:33:44:55",
        created_at=_agora(), updated_at=_agora(),
    ))
    db.commit()


def test_pacote_leva_as_quatro_secoes_com_segredos_em_claro(db: Session) -> None:
    _povoar(db)
    data = bundle_mod.build(db)

    secoes = data["sections"]
    assert set(secoes) == {"config", "environments", "users", "devices"}
    # segredos saem decifrados (é o envelope que protege, não a cifra local)
    assert secoes["config"]["uscall_servers"][0]["token"] == "tok-123"
    assert secoes["config"]["mqtt_brokers"][0]["password"] == "senha-broker"
    chaves = {c["key"]: c for c in secoes["config"]["app_config"]}
    assert chaves["webhooks.devices.token"]["value"] == "segredo-do-webhook"
    assert chaves["webhooks.devices.token"]["is_secret"] is True
    # ambientes vêm com as linhas na ordem da planilha e com a senha SIP
    amb = secoes["environments"][0]
    assert [ln["numero_ramal"] for ln in amb["lines"]] == ["1401", "1402"]
    assert amb["lines"][0]["senha_sip"] == "sip-secreta"
    assert amb["config_padrao"]  # defaults mesclados
    assert {u["username"] for u in secoes["users"]} == {"operador"}
    assert secoes["devices"][0]["name"] == "1401"


def test_passphrase_salva_nao_viaja_no_pacote(db: Session) -> None:
    """A passphrase do backup automático é local; exportá-la entregaria a
    chave dos próprios backups junto com eles."""
    save_backup_settings(db, {"export_passphrase": "frase-local"})
    data = bundle_mod.build(db, ("config",))
    chaves = {c["key"] for c in data["sections"]["config"]["app_config"]}
    assert KEY_PASSPHRASE not in chaves


def test_roundtrip_cifrado_com_troca_de_chave_da_instalacao(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exporta com uma APP_SECRET_KEY e importa com outra: é o cenário real de
    levar a configuração para outro sistema."""
    _povoar(db)
    blob = encrypt_export(bundle_mod.to_bytes(bundle_mod.build(db)), "frase-forte")

    # destino: outra instalação => outra chave local
    monkeypatch.setenv("APP_SECRET_KEY", "chave-completamente-diferente-1234")
    get_settings.cache_clear()
    for srv in uscall_repo.list_servers(db):
        uscall_repo.delete_server(db, srv.id)
    for env in ec_repo.list_environments(db):
        ec_repo.delete_environment(db, env.id)
    db.commit()

    data = bundle_mod.parse(decrypt_export(blob, "frase-forte"))
    relatorio = bundle_mod.apply(db, data, mode="merge")

    assert relatorio["applied"]["environments"] == {"ambientes": 1, "linhas": 2}
    (srv,) = uscall_repo.list_servers(db)
    # recifrado com a chave NOVA e legível nela
    assert uscall_repo.load_server_token(srv) == "tok-123"
    (broker,) = mqtt_repo.list_brokers(db)
    assert mqtt_repo.load_broker_password(broker) == "senha-broker"
    linhas = ec_repo.list_lines(db, ec_repo.list_environments(db)[0].id)
    assert [ln.senha_sip for ln in linhas] == ["sip-secreta", "outra"]


def test_replace_troca_ambientes_e_preserva_o_identificador(db: Session) -> None:
    _povoar(db)
    data = bundle_mod.build(db, ("environments",))
    id_original = data["sections"]["environments"][0]["id"]

    # o destino já tem outro ambiente, que o replace deve remover
    ec_repo.create_environment(db, nome="Antigo", modelo_telefone="HTEK UC912")
    db.commit()

    bundle_mod.apply(db, data, sections=("environments",), mode="replace")
    ambientes = ec_repo.list_environments(db)
    assert [e.id for e in ambientes] == [id_original]


def test_merge_nao_apaga_o_que_ja_existe(db: Session) -> None:
    _povoar(db)
    data = bundle_mod.build(db, ("environments",))
    ec_repo.create_environment(db, nome="Antigo", modelo_telefone="HTEK UC912")
    db.commit()

    bundle_mod.apply(db, data, sections=("environments",), mode="merge")
    nomes = sorted(e.nome for e in ec_repo.list_environments(db))
    assert nomes == ["Antigo", "Loja 14", "Loja 14"]


def test_usuario_existente_nunca_e_apagado_nem_rebaixado_em_merge(db: Session) -> None:
    _povoar(db)
    data = bundle_mod.build(db, ("users",))
    # o destino tem outro admin, com senha própria
    db.add(User(
        username="admin", password_hash="hash-do-destino", role="admin",
        created_at=_agora(),
    ))
    db.commit()

    bundle_mod.apply(db, data, sections=("users",), mode="merge")
    usuarios = {u.username: u for u in db.query(User).all()}
    assert set(usuarios) == {"admin", "operador"}
    assert usuarios["admin"].password_hash == "hash-do-destino"


def test_replace_atualiza_hash_de_usuario_existente(db: Session) -> None:
    db.add(User(username="admin", password_hash="hash-antigo", role="admin", created_at=_agora()))
    db.commit()
    data = bundle_mod.build(db, ("users",))
    db.query(User).filter(User.username == "admin").update({"password_hash": "hash-local"})
    db.commit()

    bundle_mod.apply(db, data, sections=("users",), mode="replace")
    assert db.query(User).one().password_hash == "hash-antigo"


def test_parse_recusa_formato_e_versao_desconhecidos() -> None:
    with pytest.raises(bundle_mod.BundleError):
        bundle_mod.parse(b'{"format":"outra-coisa","schema_version":1,"sections":{}}')
    with pytest.raises(bundle_mod.BundleError):
        bundle_mod.parse(b'{"format":"mwr-backup","schema_version":99,"sections":{}}')
    with pytest.raises(bundle_mod.BundleError):
        bundle_mod.parse(b"nem json")


def test_normalize_sections() -> None:
    assert bundle_mod.normalize_sections([]) == bundle_mod.SECTIONS
    assert bundle_mod.normalize_sections(["users", "config"]) == ("config", "users")
    with pytest.raises(bundle_mod.BundleError):
        bundle_mod.normalize_sections(["inexistente"])


def test_apply_recusa_modo_invalido(db: Session) -> None:
    with pytest.raises(bundle_mod.BundleError):
        bundle_mod.apply(db, bundle_mod.build(db), mode="apagar-tudo")


def test_resumo_para_a_tela(db: Session) -> None:
    _povoar(db)
    resumo = bundle_mod.summarize(bundle_mod.build(db))["sections"]
    assert resumo["environments"] == {"ambientes": 1, "linhas": 2, "nomes": ["Loja 14"]}
    assert resumo["config"]["servidores_uscall"] == 1
    assert resumo["users"]["usuarios"] == 1
