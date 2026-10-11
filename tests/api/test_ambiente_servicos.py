"""Serviços do ambiente, DVR e câmeras por ambiente — NOC 1.4.0, ``docs/AGENTE-NOC.md`` item 29
e ``CONTRATO-DO-AGENTE.md`` §13.11.

O que se prova aqui:

- a migration 0017 sobe, desce e sobe de novo num SQLite com dado, sem apagar
  verificação nem credencial (não há recriação de tabela);
- ``servicos``: padrão ``["telefone"]``; ambiente sem telefone tem modelo ""; desligar
  o telefone com ramal é recusado; desligar DVR/câmera pausa e religar retoma;
- o contrato: ``ambiente``/``canais``/``canal`` no retrato e na vista; ``de`` de NOC antigo
  (sem as chaves) confere; ``para`` sem a chave mantém; ambiente que não existe é recusado;
- o retrato leva ``ambientes[].servicos`` e o manifesto declara ``equipamentos.ambiente``;
- a planilha local (DVR/Câmeras) grava pela mesma validação do NOC, nunca devolve a
  senha e guarda a credencial cifrada.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from middleware_monitor.core.models import CredencialDeEquipamento, Equipamento, ExtensionEnvironment
from middleware_monitor.domain.equipamentos import lista, modulos, planilha_cftv
from middleware_monitor.domain.extension_configurator import repository as repo
from middleware_monitor.domain.extension_configurator import servicos as servicos_mod
from middleware_monitor.domain.noc import manifesto, retrato
from tests.api.test_extension_configurator import _authed
from tests.api.test_noc_equipamentos import _definir, _gravador, _vista
from tests.api.test_noc_tarefas import _limpo, _processar  # noqa: F401

RAIZ = Path(__file__).resolve().parents[2]
BASE = "/api/extension-configurator/environments"


@pytest.fixture
def backups(monkeypatch) -> list[str]:
    """Conta os snapshots sem tirar um de verdade (como em ``test_noc_equipamentos``)."""
    from middleware_monitor.domain.backup import snapshot

    feitos: list[str] = []

    class _Arquivo:
        name = "snapshot-noc.db"

    def criar(**_k):
        feitos.append("noc")
        return _Arquivo()

    monkeypatch.setattr(snapshot, "create_snapshot", criar)
    return feitos


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- A migration ---------------------------------------------------------------------------


def _alembic(url: str) -> Config:
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "src/middleware_monitor/core/migrations"))
    return cfg


def test_migration_0017_sobe_desce_e_sobe_sem_perder_dado(isolated_data_dir: Path) -> None:
    from middleware_monitor.settings import get_settings

    url = get_settings().effective_db_url
    cfg = _alembic(url)
    command.upgrade(cfg, "0016_cftv")
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("PRAGMA foreign_keys=ON"))
        c.execute(
            text(
                "INSERT INTO extension_environments"
                " (id, nome, modelo_telefone, config_padrao, created_at, updated_at)"
                " VALUES ('loja-1', 'Loja 1', 'HTEK UC902G', '{}', '2026-10-01', '2026-10-01')"
            )
        )
        c.execute(
            text(
                "INSERT INTO equipamentos"
                " (id, ref, tipo, nome, host, tentativas_para_cair, estado, criado_em, atualizado_em)"
                " VALUES ('eq_1', 'r1', 'gravador', 'NVR', '10.0.0.2', 3, 'desconhecido',"
                " '2026-10-01', '2026-10-01')"
            )
        )
        c.execute(
            text(
                "INSERT INTO equipamento_verificacoes"
                " (id, equipamento_id, ref, posicao, tipo, parametros, intervalo_s, estado, falhas_seguidas)"
                " VALUES ('vf_1', 'eq_1', 'ping', 0, 'icmp', '{}', 60, 'desconhecido', 0)"
            )
        )
        c.execute(
            text(
                "INSERT INTO equipamento_credenciais (equipamento_id, tipo, usuario, segredo, impressao)"
                " VALUES ('eq_1', 'HTTP', 'admin', 'enc:v1:x', 'abcdefabcdef')"
            )
        )

    command.upgrade(cfg, "head")
    insp = inspect(engine)
    assert {"servicos"} <= {c["name"] for c in insp.get_columns("extension_environments")}
    assert {"ambiente_id", "canais", "canal"} <= {c["name"] for c in insp.get_columns("equipamentos")}
    assert "ix_equipamentos_ambiente_id" in {i["name"] for i in insp.get_indexes("equipamentos")}
    with engine.begin() as c:
        assert c.execute(text("SELECT servicos FROM extension_environments")).scalar() == '["telefone"]'
        assert c.execute(text("SELECT ambiente_id FROM equipamentos")).scalar() is None
        assert c.execute(text("SELECT count(*) FROM equipamento_verificacoes")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM equipamento_credenciais")).scalar() == 1
        c.execute(text("UPDATE equipamentos SET ambiente_id = 'loja-1', canais = 8"))

    command.downgrade(cfg, "0016_cftv")
    insp = inspect(engine)
    assert "servicos" not in {c["name"] for c in insp.get_columns("extension_environments")}
    assert not {"ambiente_id", "canais", "canal"} & {c["name"] for c in insp.get_columns("equipamentos")}
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM equipamento_verificacoes")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM equipamento_credenciais")).scalar() == 1

    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # repetível
    engine.dispose()


# --- Serviços -----------------------------------------------------------------------------


def test_servicos_padrao_e_ambiente_sem_telefone(db) -> None:
    env = repo.create_environment(db, nome="Loja 1", modelo_telefone="HTEK UC902G")
    assert servicos_mod.servicos_de(env) == ["telefone"]
    sem = repo.create_environment(db, nome="CD", modelo_telefone="HTEK UC902G", servicos=["camera", "dvr"])
    assert servicos_mod.servicos_de(sem) == ["dvr", "camera"]  # ordem fixa
    assert sem.modelo_telefone == ""
    with pytest.raises(servicos_mod.ServicosInvalidos):
        repo.create_environment(db, nome="X", modelo_telefone="HTEK UC902G", servicos=[])
    with pytest.raises(servicos_mod.ServicosInvalidos):
        repo.create_environment(db, nome="X", modelo_telefone="HTEK UC902G", servicos=["fax"])


def test_desligar_telefone_com_ramal_e_recusado(db) -> None:
    env = repo.create_environment(db, nome="Loja 1", modelo_telefone="HTEK UC902G")
    repo.save_lines(db, env, [repo.new_line(ip="10.0.0.5", numero_ramal="101")])
    db.commit()
    with pytest.raises(repo.ServicoRecusado, match="ramal"):
        repo.definir_servicos(db, env, ["dvr"])
    db.rollback()
    vazio = repo.create_environment(db, nome="Loja 2", modelo_telefone="HTEK UC902G")
    repo.definir_servicos(db, vazio, ["dvr"])
    assert vazio.modelo_telefone == ""
    with pytest.raises(repo.ServicoRecusado, match="modelo"):
        repo.definir_servicos(db, vazio, ["telefone", "dvr"])
    repo.definir_servicos(db, vazio, ["telefone", "dvr"], modelo_telefone="Yealink T31G")
    assert vazio.modelo_telefone == "Yealink T31G"


def _ligar_intelbras(db) -> None:
    modulos.definir(db, {"cftv.intelbras": True}, por="local:teste")
    db.commit()


def _dvr_no_ambiente(db, env_id: str) -> str:
    para = [
        {
            "id": None,
            "ref": "nvr",
            "tipo": "gravador",
            "nome": "NVR",
            "host": "10.0.0.2",
            "ambiente": env_id,
            "verificacoes": [{"id": None, "ref": "ping", "tipo": "icmp", "parametros": {}}],
        }
    ]
    validado = lista.validar_para(para, lista.vista(db), aceitos=modulos.aceitos(db), ambientes={env_id})
    g = lista.gravar(db, validado, _agora(), aceitos=modulos.aceitos(db))
    db.commit()
    return g.ids_por_ref["nvr"]


def test_desligar_dvr_pausa_e_religar_retoma(db) -> None:
    env = repo.create_environment(db, nome="Loja 1", modelo_telefone="", servicos=["dvr"])
    db.commit()
    eid = _dvr_no_ambiente(db, env.id)
    eq = db.get(Equipamento, eid)
    assert eq.ambiente_id == env.id and eq.verificacoes[0].estado == "desconhecido"
    repo.definir_servicos(db, env, ["camera"])
    db.commit()
    db.expire_all()
    eq = db.get(Equipamento, eid)
    assert eq is not None, "desligar não apaga"
    assert [v.estado for v in eq.verificacoes] == ["pausada"]
    repo.definir_servicos(db, env, ["camera", "dvr"])
    db.commit()
    db.expire_all()
    assert [v.estado for v in db.get(Equipamento, eid).verificacoes] == ["desconhecido"]


def test_apagar_ambiente_deixa_o_equipamento_sem_ambiente(db) -> None:
    env = repo.create_environment(db, nome="Loja 1", modelo_telefone="", servicos=["dvr"])
    db.commit()
    eid = _dvr_no_ambiente(db, env.id)
    repo.delete_environment(db, env.id)
    db.commit()
    db.expire_all()
    assert db.get(Equipamento, eid).ambiente_id is None


# --- O contrato do definir_equipamentos ------------------------------------------------------


def test_vista_leva_ambiente_canais_e_canal(db) -> None:
    env = repo.create_environment(db, nome="Loja 1", modelo_telefone="", servicos=["dvr"])
    db.commit()
    _dvr_no_ambiente(db, env.id)
    [item] = lista.vista(db)
    assert (item["ambiente"], item["canais"], item["canal"]) == (env.id, None, None)


def test_de_sem_as_chaves_novas_confere_e_com_a_chave_compara(db) -> None:
    env = repo.create_environment(db, nome="Loja 1", modelo_telefone="", servicos=["dvr"])
    db.commit()
    _dvr_no_ambiente(db, env.id)
    atual = lista.vista(db)
    antigo = [{k: v for k, v in i.items() if k not in ("ambiente", "canais", "canal")} for i in atual]
    assert lista.confere(antigo, atual)
    assert lista.confere(atual, atual)
    outro = [{**atual[0], "ambiente": None}]
    assert not lista.confere(outro, atual)
    assert not lista.confere([{**atual[0], "canais": 16}], atual)


async def test_noc_antigo_edita_sem_apagar_o_ambiente(db, backups) -> None:
    env = repo.create_environment(db, nome="Loja 1", modelo_telefone="", servicos=["dvr"])
    db.commit()
    _dvr_no_ambiente(db, env.id)
    de = [{k: v for k, v in i.items() if k not in ("ambiente", "canais", "canal")} for i in _vista(db)]
    para = [{**{k: v for k, v in de[0].items() if k != "paiId"}, "paiRef": None, "nome": "NVR novo"}]
    pronta = await _processar(_definir(de, para, "antigo"))
    assert pronta.corpo["ok"] is True, pronta.corpo
    assert pronta.corpo["resultado"]["alterados"] == 1
    [item] = _vista(db)
    assert (item["nome"], item["ambiente"]) == ("NVR novo", env.id)


async def test_noc_novo_muda_o_ambiente_e_recusa_o_que_nao_existe(db, backups) -> None:
    a = repo.create_environment(db, nome="Loja 1", modelo_telefone="", servicos=["dvr"])
    b = repo.create_environment(db, nome="Loja 2", modelo_telefone="", servicos=["dvr"])
    db.commit()
    _dvr_no_ambiente(db, a.id)
    de = _vista(db)
    base = {k: v for k, v in de[0].items() if k != "paiId"}
    pronta = await _processar(_definir(de, [{**base, "paiRef": None, "ambiente": "nao-existe"}], "x1"))
    assert pronta.corpo["ok"] is False
    assert pronta.corpo["resultado"]["recusa"] == "VALOR_INVALIDO"
    assert "nao-existe" in pronta.corpo["resultado"]["motivo"]
    pronta = await _processar(_definir(de, [{**base, "paiRef": None, "ambiente": b.id, "canais": 8}], "x2"))
    assert pronta.corpo["ok"] is True, pronta.corpo
    [item] = _vista(db)
    assert (item["ambiente"], item["canais"]) == (b.id, 8)


def test_canal_so_em_camera_e_canais_so_em_gravador(db) -> None:
    with pytest.raises(lista.ForaDeForma, match="canal só em equipamento camera"):
        lista.validar_para([{**_gravador(), "canal": 2}], [])
    with pytest.raises(lista.ForaDeForma, match="canais só em equipamento gravador"):
        lista.validar_para(
            [{"id": None, "ref": "c", "tipo": "camera", "nome": "C", "host": "10.0.0.9", "canais": 4}], []
        )
    with pytest.raises(lista.ForaDeForma, match="1 a 256"):
        lista.validar_para([{**_gravador(), "canais": 0}], [])


# --- Retrato e manifesto -----------------------------------------------------------------------


def test_retrato_leva_servicos_e_ambiente_sem_telefone_nao_tem_modelo(db) -> None:
    repo.create_environment(db, nome="Loja 1", modelo_telefone="HTEK UC902G")
    repo.create_environment(db, nome="CD", modelo_telefone="HTEK UC902G", servicos=["camera"])
    db.commit()
    por_nome = {a["nome"]: a for a in retrato.ambientes(db)}
    assert por_nome["Loja 1"]["servicos"] == ["telefone"]
    assert por_nome["Loja 1"]["modelo"] == "HTEK UC902G" and por_nome["Loja 1"]["secoes"]
    assert por_nome["CD"]["servicos"] == ["camera"]
    assert (por_nome["CD"]["modelo"], por_nome["CD"]["secoes"]) == (None, [])


def test_manifesto_declara_a_capacidade_e_nao_conta_modelo_vazio(db) -> None:
    repo.create_environment(db, nome="Loja 1", modelo_telefone="HTEK UC902G")
    repo.create_environment(db, nome="CD", modelo_telefone="", servicos=["dvr"])
    db.commit()
    corpo = manifesto.montar(db)
    assert corpo["capacidades"] == ["equipamentos.ambiente", "ambientes.gerir"]
    assert [m["modelo"] for m in corpo["modelos"]] == ["HTEK UC902G"]


# --- A tela (API local) ------------------------------------------------------------------------


def test_api_servicos_e_planilha_de_dvr_e_cameras(client, db) -> None:
    csrf = _authed(client, db)
    h = {"X-CSRF-Token": csrf}
    _ligar_intelbras(db)
    r = client.post(BASE, json={"nome": "Loja 9", "servicos": ["dvr", "camera"]}, headers=h)
    assert r.status_code == 200, r.text
    env_id = r.json()["id"]
    assert r.json()["servicos"] == ["dvr", "camera"] and r.json()["modelo_telefone"] == ""

    r = client.put(f"{BASE}/{env_id}/lines", json={"linhas": []}, headers=h)
    assert r.status_code == 409

    pedido = {
        "dvr": [
            {
                "ref": "mw_aaaaaaaaaaaa",
                "nome": "DVR Frente",
                "ip": "10.0.0.20",
                "portaHttp": 8080,
                "portaRtsp": 554,
                "adaptador": "intelbras",
                "modelo": "MHDX 1216",
                "usuario": "admin",
                "senha": "segredo-do-dvr",
                "canais": 16,
            }
        ],
        "camera": [
            {
                "nome": "Caixa 1",
                "ip": "10.0.0.31",
                "dvr": "mw_aaaaaaaaaaaa",
                "canal": 3,
                "fabricante": "Intelbras",
            }
        ],
    }
    r = client.put(f"{BASE}/{env_id}/equipamentos", json=pedido, headers=h)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["adaptadores"] == ["intelbras"]
    [dvr] = corpo["dvr"]
    assert (dvr["portaHttp"], dvr["portaRtsp"], dvr["canais"], dvr["usuario"]) == (8080, 554, 16, "admin")
    assert dvr["senha"] == "" and dvr["senhaGuardada"] is True
    assert "segredo-do-dvr" not in r.text
    [cam] = corpo["camera"]
    assert (cam["dvr"], cam["canal"], cam["fabricante"]) == ("mw_aaaaaaaaaaaa", 3, "Intelbras")

    db.expire_all()
    [cred] = db.query(CredencialDeEquipamento).all()
    assert cred.segredo.startswith("enc:v1:") and "segredo-do-dvr" not in cred.segredo
    eq = db.get(Equipamento, dvr["id"])
    assert sorted((v.ref, v.tipo) for v in eq.verificacoes) == [
        ("cftv", "cftv"),
        ("icmp", "icmp"),
        ("rtsp", "tcp"),
    ]
    assert db.get(Equipamento, cam["id"]).pai_id == dvr["id"]

    # Senha em branco mantém; RTSP em branco tira a verificação; o retrato do NOC vê tudo.
    pedido["dvr"][0] = {**pedido["dvr"][0], "id": dvr["id"], "senha": "", "portaRtsp": ""}
    pedido["camera"][0] = {**pedido["camera"][0], "id": cam["id"]}
    r = client.put(f"{BASE}/{env_id}/equipamentos", json=pedido, headers=h)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(CredencialDeEquipamento).one().impressao == cred.impressao
    assert sorted(v.ref for v in db.get(Equipamento, dvr["id"]).verificacoes) == ["cftv", "icmp"]
    retrato_eq = {e["ref"]: e for e in lista.retrato(db)}
    assert retrato_eq["mw_aaaaaaaaaaaa"]["ambiente"] == env_id
    assert retrato_eq[cam["ref"]]["canal"] == 3

    # Lista de ambientes conta DVR e câmera.
    [resumo] = [e for e in client.get(BASE).json()["environments"] if e["id"] == env_id]
    assert (resumo["dvrs"], resumo["cameras"], resumo["servicos"]) == (1, 1, ["dvr", "camera"])

    # Tirar a câmera da planilha apaga só ela.
    r = client.put(f"{BASE}/{env_id}/equipamentos", json={"camera": []}, headers=h)
    assert r.status_code == 200 and r.json()["camera"] == [] and len(r.json()["dvr"]) == 1


def test_api_recusa_aba_desligada_adaptador_nao_aceito_e_linha_ruim(client, db) -> None:
    csrf = _authed(client, db)
    h = {"X-CSRF-Token": csrf}
    r = client.post(BASE, json={"nome": "Loja 9", "servicos": ["dvr"]}, headers=h)
    env_id = r.json()["id"]
    r = client.put(f"{BASE}/{env_id}/equipamentos", json={"camera": []}, headers=h)
    assert r.status_code == 409
    linha = {"nome": "DVR", "ip": "10.0.0.20", "adaptador": "intelbras"}
    r = client.put(f"{BASE}/{env_id}/equipamentos", json={"dvr": [linha]}, headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["aba"] == "dvr" and r.json()["detail"]["linha"] == 1
    assert "cftv.intelbras" in r.json()["detail"]["mensagem"]
    _ligar_intelbras(db)
    r = client.put(f"{BASE}/{env_id}/equipamentos", json={"dvr": [{**linha, "ip": "não é ip"}]}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["linha"] == 1
    r = client.put(f"{BASE}/{env_id}/equipamentos", json={"dvr": [{**linha, "usuario": "admin"}]}, headers=h)
    assert r.status_code == 422 and "senha" in r.json()["detail"]["mensagem"]
    # Sem CSRF, nada.
    assert client.put(f"{BASE}/{env_id}/equipamentos", json={"dvr": []}).status_code == 403


def test_api_servicos_desligar_telefone_com_ramal_e_409(client, db) -> None:
    csrf = _authed(client, db)
    h = {"X-CSRF-Token": csrf}
    env = repo.create_environment(db, nome="Loja 1", modelo_telefone="HTEK UC902G")
    repo.save_lines(db, env, [repo.new_line(ip="10.0.0.5", numero_ramal="101")])
    db.commit()
    r = client.put(f"{BASE}/{env.id}/servicos", json={"servicos": ["dvr"]}, headers=h)
    assert r.status_code == 409
    r = client.put(f"{BASE}/{env.id}/servicos", json={"servicos": ["telefone", "dvr"]}, headers=h)
    assert r.status_code == 200 and r.json()["servicos"] == ["telefone", "dvr"]
    db.expire_all()
    assert json.loads(db.get(ExtensionEnvironment, env.id).servicos) == ["telefone", "dvr"]


def test_planilha_nao_mexe_no_que_nao_e_do_ambiente(db) -> None:
    env = repo.create_environment(db, nome="Loja 1", modelo_telefone="", servicos=["dvr", "camera"])
    db.commit()
    _ligar_intelbras(db)
    # Um switch do NOC, sem ambiente, e um gravador de outro ambiente.
    outro = repo.create_environment(db, nome="Loja 2", modelo_telefone="", servicos=["dvr"])
    db.commit()
    _dvr_no_ambiente(db, outro.id)
    para = [{**lista.vista(db)[0], "paiRef": None}]
    para[0].pop("paiId")
    para.append({"id": None, "ref": "sw", "tipo": "switch", "nome": "SW", "host": "10.0.0.1"})
    lista.gravar(db, lista.validar_para(para, lista.vista(db)), _agora())
    db.commit()
    planilha_cftv.gravar(db, env, {"dvr": [], "camera": []}, _agora())
    db.commit()
    assert sorted(i["ref"] for i in lista.vista(db)) == ["nvr", "sw"]
