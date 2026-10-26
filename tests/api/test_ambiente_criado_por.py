"""Quem criou o ambiente (2.19.0) — relatório de criação de ambientes do NOC.

O que se prova aqui:

- a migration 0019 sobe, desce e sobe; ambiente que já existia fica com autor e via NULL;
- cada caminho que cria ambiente grava ``criado_por``/``criado_via``: a tela daqui (``local``,
  o usuário logado), a tela de dispositivos (``dispositivos``), duplicar e importar (``local``)
  e a tarefa do NOC (``noc``, o e-mail de quem pediu);
- pedido com o cabeçalho do túnel e sessão viva neste processo → ``tunel`` + o e-mail do
  operador; cabeçalho forjado, de sessão encerrada ou vindo de outra máquina → regra local;
- o retrato leva ``criadoEm``, ``criadoPor`` e ``criadoVia`` (null quando desconhecido).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text

from middleware_monitor.api import auth as auth_api
from middleware_monitor.core.models import ExtensionEnvironment
from middleware_monitor.domain.devices.repository import upsert_from_uscall
from middleware_monitor.domain.extension_configurator import repository as repo
from middleware_monitor.domain.noc import retrato, tunel
from tests.api.test_extension_configurator import _authed
from tests.api.test_noc_tarefas import _limpo, _processar, _tarefa  # noqa: F401

RAIZ = Path(__file__).resolve().parents[2]
BASE = "/api/extension-configurator/environments"
SESSAO = "sessao-do-tunel-1"
OPERADOR = "ana.souza@workconnect.com.br"


def _env(db, env_id: str) -> ExtensionEnvironment:
    db.expire_all()
    env = db.get(ExtensionEnvironment, env_id)
    assert env is not None
    return env


@pytest.fixture
def sessao_viva() -> Iterator[tunel.Sessao]:
    """Uma sessão de túnel registrada como aberta neste processo (sem rede: só o registro)."""
    destino = tunel.destino_da_lan("127.0.0.1", 8080, "http")
    sessao = tunel.Sessao(
        SESSAO, destino, canal="https://noc.exemplo", credencial="ag.x",
        operador=f"noc:{OPERADOR}", pedida_por=OPERADOR,
    )
    tunel._sessoes[SESSAO] = sessao
    yield sessao
    tunel._sessoes.pop(SESSAO, None)


@pytest.fixture
def desta_maquina(monkeypatch) -> None:
    """O TestClient chega como ``testclient``; pelo túnel o pedido vem desta máquina."""
    monkeypatch.setattr(auth_api, "_ips_desta_maquina", lambda: {"127.0.0.1", "testclient"})


# --- A migration ---------------------------------------------------------------------------


def test_migration_0019_sobe_desce_e_sobe_sem_inventar_autor(isolated_data_dir: Path) -> None:
    from middleware_monitor.settings import get_settings

    url = get_settings().effective_db_url
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "src/middleware_monitor/core/migrations"))
    command.upgrade(cfg, "0018_indice_message_id")
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO extension_environments"
                " (id, nome, modelo_telefone, config_padrao, created_at, updated_at)"
                " VALUES ('antigo', 'Antigo', 'HTEK UC902G', '{}', '2026-10-01', '2026-10-01')"
            )
        )
    command.upgrade(cfg, "head")
    colunas = {c["name"] for c in inspect(engine).get_columns("extension_environments")}
    assert {"criado_por", "criado_via"} <= colunas
    with engine.begin() as c:
        linha = c.execute(text("SELECT criado_por, criado_via FROM extension_environments")).one()
        assert tuple(linha) == (None, None)
    command.downgrade(cfg, "0018_indice_message_id")
    colunas = {c["name"] for c in inspect(engine).get_columns("extension_environments")}
    assert not {"criado_por", "criado_via"} & colunas
    command.upgrade(cfg, "head")
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM extension_environments")).scalar() == 1
    engine.dispose()


# --- A tela daqui --------------------------------------------------------------------------


def test_tela_local_grava_o_usuario_logado(client, db) -> None:
    csrf = _authed(client, db)
    r = client.post(BASE, json={"nome": "Loja Centro"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    env = _env(db, r.json()["id"])
    assert (env.criado_por, env.criado_via) == ("admin", "local")


def test_tunel_com_sessao_viva_grava_o_operador(client, db, sessao_viva, desta_maquina) -> None:
    csrf = _authed(client, db)
    r = client.post(
        BASE, json={"nome": "Loja Norte"},
        headers={"X-CSRF-Token": csrf, tunel.CABECALHO_DO_TUNEL: SESSAO},
    )
    assert r.status_code == 200, r.text
    env = _env(db, r.json()["id"])
    assert (env.criado_por, env.criado_via) == (OPERADOR, "tunel")


def test_cabecalho_forjado_cai_na_regra_local(client, db, sessao_viva, desta_maquina) -> None:
    csrf = _authed(client, db)
    r = client.post(
        BASE, json={"nome": "Loja Sul"},
        headers={"X-CSRF-Token": csrf, tunel.CABECALHO_DO_TUNEL: "sessao-que-nao-existe"},
    )
    assert r.status_code == 200, r.text
    env = _env(db, r.json()["id"])
    assert (env.criado_por, env.criado_via) == ("admin", "local")


def test_sessao_encerrada_nao_vale(client, db, sessao_viva, desta_maquina) -> None:
    sessao_viva.encerrada = True
    csrf = _authed(client, db)
    r = client.post(
        BASE, json={"nome": "Loja Leste"},
        headers={"X-CSRF-Token": csrf, tunel.CABECALHO_DO_TUNEL: SESSAO},
    )
    env = _env(db, r.json()["id"])
    assert (env.criado_por, env.criado_via) == ("admin", "local")


def test_cabecalho_de_outra_maquina_nao_vale(client, db, sessao_viva) -> None:
    # Sem ``desta_maquina``: o pedido chega de ``testclient``, que não é esta máquina.
    csrf = _authed(client, db)
    r = client.post(
        BASE, json={"nome": "Loja Oeste"},
        headers={"X-CSRF-Token": csrf, tunel.CABECALHO_DO_TUNEL: SESSAO},
    )
    env = _env(db, r.json()["id"])
    assert (env.criado_por, env.criado_via) == ("admin", "local")


def test_duplicar_e_importar_gravam_a_autoria_do_pedido(client, db, sessao_viva, desta_maquina) -> None:
    csrf = _authed(client, db)
    client.post(BASE, json={"nome": "Matriz"}, headers={"X-CSRF-Token": csrf})
    dup = client.post(
        f"{BASE}/matriz/duplicate", json={"nome": "Filial"},
        headers={"X-CSRF-Token": csrf, tunel.CABECALHO_DO_TUNEL: SESSAO},
    )
    assert dup.status_code == 200, dup.text
    env = _env(db, dup.json()["id"])
    assert (env.criado_por, env.criado_via) == (OPERADOR, "tunel")

    blob = client.post(
        f"{BASE}/matriz/export", json={"passphrase": "frase"}, headers={"X-CSRF-Token": csrf}
    ).text
    imp = client.post(
        f"{BASE}/import", json={"passphrase": "frase", "blob": blob, "nome": "Importado"},
        headers={"X-CSRF-Token": csrf},
    )
    assert imp.status_code == 200, imp.text
    env = _env(db, imp.json()["id"])
    assert (env.criado_por, env.criado_via) == ("admin", "local")


# --- A tela de dispositivos ----------------------------------------------------------------


def _dispositivos(db) -> list[int]:
    from middleware_monitor.core.models import Device

    upsert_from_uscall(db, [{"ramal": "3001", "status": "disponivel", "ip": "10.0.0.10"}])
    db.commit()
    return [d.id for d in db.scalars(select(Device)).all()]


def test_dispositivos_local(client, db) -> None:
    csrf = _authed(client, db)
    ids = _dispositivos(db)
    r = client.post(
        "/api/devices/bulk/create-environment",
        json={"nome": "Da tela de dispositivos", "device_ids": ids},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    env = _env(db, r.json()["environment_id"])
    assert (env.criado_por, env.criado_via) == ("admin", "dispositivos")


def test_dispositivos_pelo_tunel(client, db, sessao_viva, desta_maquina) -> None:
    csrf = _authed(client, db)
    ids = _dispositivos(db)
    r = client.post(
        "/api/devices/bulk/create-environment",
        json={"nome": "Dispositivos pelo NOC", "device_ids": ids},
        headers={"X-CSRF-Token": csrf, tunel.CABECALHO_DO_TUNEL: SESSAO},
    )
    assert r.status_code == 200, r.text
    env = _env(db, r.json()["environment_id"])
    assert (env.criado_por, env.criado_via) == (OPERADOR, "dispositivos")


# --- A tarefa do NOC -----------------------------------------------------------------------


async def test_tarefa_do_noc_grava_o_email_de_quem_pediu(db) -> None:
    tarefa = _tarefa(
        "c1", tipo="criar_ambiente", raio="ESCRITA_REVERSIVEL",
        pedido={"nome": "Loja do NOC", "modeloTelefone": "HTEK UC902G"},
    )
    pronta = await _processar(tarefa)
    if pronta.corpo["ok"] is not True:
        pytest.fail(f"criar_ambiente falhou: {pronta.corpo}")
    env = _env(db, pronta.corpo["resultado"]["ambienteId"])
    assert (env.criado_por, env.criado_via) == ("ana@workconnect.com.br", "noc")


def test_sessao_sem_email_a_parte_usa_o_operador_sem_prefixo() -> None:
    destino = tunel.destino_da_lan("127.0.0.1", 8080, "http")
    tunel._sessoes["s-velha"] = tunel.Sessao(
        "s-velha", destino, canal="https://noc", credencial="x", operador="noc:bia@x.com"
    )
    tunel._sessoes["s-anonima"] = tunel.Sessao(
        "s-anonima", destino, canal="https://noc", credencial="x", operador="noc:desconhecido"
    )
    try:
        assert tunel.operador_da_sessao("s-velha") == "bia@x.com"
        assert tunel.operador_da_sessao("s-anonima") is None
        assert tunel.operador_da_sessao("") is None
        assert tunel.operador_da_sessao("nao-existe") is None
    finally:
        tunel._sessoes.pop("s-velha", None)
        tunel._sessoes.pop("s-anonima", None)


# --- O retrato -----------------------------------------------------------------------------


def test_retrato_leva_a_autoria_e_null_quando_desconhecida(db) -> None:
    repo.create_environment(db, nome="Antigo", modelo_telefone="HTEK UC902G")
    repo.create_environment(
        db, nome="Novo", modelo_telefone="HTEK UC902G", criado_por=OPERADOR, criado_via="tunel"
    )
    db.commit()
    por_nome = {a["nome"]: a for a in retrato.ambientes(db)}
    antigo, novo = por_nome["Antigo"], por_nome["Novo"]
    assert (antigo["criadoPor"], antigo["criadoVia"]) == (None, None)
    assert isinstance(antigo["criadoEm"], str)
    assert (novo["criadoPor"], novo["criadoVia"]) == (OPERADOR, "tunel")
    env = db.scalar(select(ExtensionEnvironment).where(ExtensionEnvironment.nome == "Novo"))
    assert env is not None
    assert novo["criadoEm"] == env.created_at.replace(tzinfo=None).isoformat(timespec="seconds")
