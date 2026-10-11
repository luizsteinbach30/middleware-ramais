"""O NOC cria e apaga ambientes (2.17.1, capacidade ``ambientes.gerir``).

O que se prova aqui:

- ``criar_ambiente`` grava o ambiente com backup antes, e nome repetido (sem diferença de
  maiúscula nem de espaço) é recusado; serviços e modelo do telefone são conferidos com a mesma
  lista da tela local — que o manifesto publica em ``modelosDeTelefone``;
- ``apagar_ambiente`` confere o ``de`` (nome e quantidade de ramais) antes e depois do backup;
  vão junto os ramais, o equipamento fica sem ambiente; aplicação em andamento trava;
- nenhum dos dois toca o aparelho.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from middleware_monitor.core.models import Equipamento, ExtensionEnvironment, ExtensionLine
from middleware_monitor.domain.backup import snapshot
from middleware_monitor.domain.extension_configurator import repository as repo
from middleware_monitor.domain.extension_configurator import run_state
from middleware_monitor.domain.extension_configurator.defaults import PHONE_MODELS
from middleware_monitor.domain.noc import executor, manifesto
from tests.api.test_noc_tarefas import _ambiente, _limpo, _processar, _tarefa  # noqa: F401

CRIAR = "criar_ambiente"
APAGAR = "apagar_ambiente"
ESCRITA = "ESCRITA_REVERSIVEL"


@pytest.fixture(autouse=True)
def _sem_aparelho(monkeypatch):
    """Qualquer caminho até o telefone quebra o teste."""

    async def proibido(*_a, **_k):
        raise AssertionError("criar e apagar ambiente não tocam o aparelho")

    monkeypatch.setattr("middleware_monitor.domain.extension_configurator.apply.run_apply", proibido)
    monkeypatch.setattr(
        "middleware_monitor.domain.extension_configurator.actions.run_action_on_line", proibido
    )


def _criar(pedido: dict, id_: str = "c1") -> dict:
    return _tarefa(id_, tipo=CRIAR, raio=ESCRITA, pedido=pedido)


def _apagar(env_id: str, de: dict, id_: str = "a1") -> dict:
    return _tarefa(id_, tipo=APAGAR, raio=ESCRITA, pedido={"ambienteId": env_id, "de": de})


def _ambientes(db) -> list[ExtensionEnvironment]:
    db.expire_all()
    return list(db.query(ExtensionEnvironment).all())


# --- A forma ------------------------------------------------------------------------------------


def test_os_verbos_sao_escrita_e_o_pedido_e_fechado() -> None:
    assert executor.ACOES[CRIAR].raio == ESCRITA and executor.ACOES[APAGAR].raio == ESCRITA
    extra = executor.conferir(CRIAR, ESCRITA, {"nome": "Loja", "configPadrao": {}})
    assert extra is not None and "não aceita: configPadrao" in (extra.erro or "")
    extra = executor.conferir(APAGAR, ESCRITA, {"ambienteId": "x", "de": {}, "forcar": True})
    assert extra is not None and "não aceita: forcar" in (extra.erro or "")
    faltando = executor.conferir(APAGAR, ESCRITA, {"ambienteId": "x"})
    assert faltando is not None and "precisa de: de" in (faltando.erro or "")
    leitura = executor.conferir(CRIAR, "LEITURA", {"nome": "Loja"})
    assert leitura is not None and leitura.nao_suportado


def test_manifesto_declara_a_capacidade_e_os_modelos(db) -> None:
    corpo = manifesto.montar(db)
    assert "ambientes.gerir" in corpo["capacidades"] and "equipamentos.ambiente" in corpo["capacidades"]
    assert {CRIAR, APAGAR} <= set(corpo["acoes"])
    assert corpo["modelosDeTelefone"] == [{"modelo": m, "rotulo": m} for m in PHONE_MODELS]


# --- Criar --------------------------------------------------------------------------------------


async def test_cria_com_telefone_e_backup(db, monkeypatch) -> None:
    backups = []
    original = snapshot.create_snapshot
    monkeypatch.setattr(snapshot, "create_snapshot", lambda **k: backups.append(k) or original(**k))
    pronta = await _processar(_criar({"nome": "  Loja Centro  ", "modeloTelefone": "Yealink T31G"}))
    assert pronta.corpo["ok"] is True, pronta.corpo
    r = pronta.corpo["resultado"]
    assert (r["nome"], r["servicos"], r["modeloTelefone"]) == ("Loja Centro", ["telefone"], "Yealink T31G")
    assert backups and r["backup"]
    env = db.get(ExtensionEnvironment, r["ambienteId"])
    assert env is not None and env.nome == "Loja Centro" and env.modelo_telefone == "Yealink T31G"


async def test_cria_sem_telefone_sem_modelo(db) -> None:
    pronta = await _processar(_criar({"nome": "CD", "servicos": ["camera", "dvr"]}))
    assert pronta.corpo["ok"] is True, pronta.corpo
    r = pronta.corpo["resultado"]
    assert (r["servicos"], r["modeloTelefone"]) == (["dvr", "camera"], None)


async def test_nome_repetido_e_recusado_sem_diferenca_de_maiuscula(db) -> None:
    env = _ambiente(db)  # "Loja 7"
    pronta = await _processar(_criar({"nome": " LOJA 7 ", "modeloTelefone": "HTEK UC902G"}))
    assert pronta.corpo["resultado"]["recusa"] == "AMBIENTE_JA_EXISTE"
    assert pronta.corpo["resultado"]["ambienteId"] == env.id
    assert len(_ambientes(db)) == 1


@pytest.mark.parametrize(
    "pedido",
    [
        {"nome": "Loja", "servicos": ["telefone", "fax"], "modeloTelefone": "HTEK UC902G"},
        {"nome": "Loja", "servicos": [], "modeloTelefone": "HTEK UC902G"},
        {"nome": "Loja", "servicos": "telefone", "modeloTelefone": "HTEK UC902G"},
        {"nome": "Loja"},
        {"nome": "Loja", "modeloTelefone": "Cisco 7960"},
        {"nome": "Loja", "servicos": ["telefone", "dvr"], "modeloTelefone": ""},
        {"nome": "   ", "modeloTelefone": "HTEK UC902G"},
        {"nome": "x" * 121, "modeloTelefone": "HTEK UC902G"},
        {"nome": 7, "modeloTelefone": "HTEK UC902G"},
        {"nome": "CD", "servicos": ["dvr"], "modeloTelefone": 3},
    ],
)
async def test_pedido_invalido_e_recusado_sem_backup(db, monkeypatch, pedido) -> None:
    def proibido(**_):
        raise AssertionError("recusa de forma não faz backup")

    monkeypatch.setattr(snapshot, "create_snapshot", proibido)
    pronta = await _processar(_criar(pedido))
    assert pronta.corpo["naoSuportado"] is True
    assert pronta.corpo["resultado"]["recusa"] == "VALOR_INVALIDO", pronta.corpo
    assert _ambientes(db) == []


async def test_modelo_invalido_devolve_a_lista(db) -> None:
    pronta = await _processar(_criar({"nome": "Loja", "modeloTelefone": "Cisco 7960"}))
    assert pronta.corpo["resultado"]["modelos"] == list(PHONE_MODELS)


async def test_criar_sem_backup_nao_grava(db, monkeypatch) -> None:
    def quebra(**_):
        raise snapshot.SnapshotError("disco cheio")

    monkeypatch.setattr(snapshot, "create_snapshot", quebra)
    pronta = await _processar(_criar({"nome": "Loja", "modeloTelefone": "HTEK UC902G"}))
    assert pronta.corpo["ok"] is False and "Backup obrigatório" in pronta.corpo["erro"]
    assert _ambientes(db) == []


# --- Apagar -------------------------------------------------------------------------------------


async def test_apaga_com_ramais_e_o_equipamento_fica_sem_ambiente(db) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"), ("1002", "10.0.0.12"))
    agora = datetime(2026, 10, 11, 12, 0, 0)
    db.add(
        Equipamento(
            id="eq_0000000000000001", ref="r1", tipo="dvr", nome="DVR", host="10.0.0.50",
            ambiente_id=env.id, criado_em=agora, atualizado_em=agora,
        )
    )
    db.commit()
    pronta = await _processar(_apagar(env.id, {"nome": "Loja 7", "linhas": 2}))
    assert pronta.corpo["ok"] is True, pronta.corpo
    r = pronta.corpo["resultado"]
    assert (r["ambienteId"], r["nome"], r["linhasApagadas"]) == (env.id, "Loja 7", 2)
    assert r["backup"]
    db.expire_all()
    assert db.get(ExtensionEnvironment, r["ambienteId"]) is None
    assert db.query(ExtensionLine).count() == 0
    eq = db.get(Equipamento, "eq_0000000000000001")
    assert eq is not None and eq.ambiente_id is None


async def test_apaga_ambiente_sem_telefone(db) -> None:
    env = repo.create_environment(db, nome="CD", modelo_telefone="", servicos=["camera"])
    db.commit()
    pronta = await _processar(_apagar(env.id, {"nome": "CD", "linhas": 0}))
    assert pronta.corpo["ok"] is True, pronta.corpo


@pytest.mark.parametrize("de", [{"nome": "Loja 8", "linhas": 1}, {"nome": "Loja 7", "linhas": 2}])
async def test_de_divergente_recusa_sem_apagar(db, de) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    pronta = await _processar(_apagar(env.id, de))
    assert pronta.corpo["resultado"]["recusa"] == "DE_DIVERGENTE", pronta.corpo
    assert pronta.corpo["resultado"]["atual"] == {"nome": "Loja 7", "linhas": 1}
    assert len(_ambientes(db)) == 1


@pytest.mark.parametrize(
    "de", [{"nome": "Loja 7"}, {"nome": "Loja 7", "linhas": True}, {"nome": "Loja 7", "linhas": -1}, []]
)
async def test_de_fora_de_forma(db, de) -> None:
    env = _ambiente(db)
    pronta = await _processar(_apagar(env.id, de))
    assert pronta.corpo["resultado"]["recusa"] == "VALOR_INVALIDO"
    assert len(_ambientes(db)) == 1


async def test_apagar_o_que_nao_existe(db) -> None:
    pronta = await _processar(_apagar("nao-existe", {"nome": "X", "linhas": 0}))
    assert pronta.corpo["resultado"]["recusa"] == "AMBIENTE_NAO_ENCONTRADO"


async def test_aplicacao_em_andamento_trava(db, monkeypatch) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    rs = run_state.RunState(run_id="r-teste", env_id=env.id, db_run_id=0)
    monkeypatch.setitem(run_state._RUNS, rs.run_id, rs)
    pronta = await _processar(_apagar(env.id, {"nome": "Loja 7", "linhas": 1}))
    assert pronta.corpo["resultado"]["recusa"] == "APLICACAO_EM_ANDAMENTO"
    assert len(_ambientes(db)) == 1


async def test_mudanca_durante_o_backup_e_recusada(db, monkeypatch) -> None:
    env = _ambiente(db, ("1001", "10.0.0.11"))
    original = snapshot.create_snapshot

    def backup_e_loja_mexe(**k):
        arquivo = original(**k)
        db.get(ExtensionEnvironment, env.id).nome = "Loja 70"
        db.commit()
        return arquivo

    monkeypatch.setattr(snapshot, "create_snapshot", backup_e_loja_mexe)
    pronta = await _processar(_apagar(env.id, {"nome": "Loja 7", "linhas": 1}))
    assert pronta.corpo["resultado"]["recusa"] == "DE_DIVERGENTE"
    assert len(_ambientes(db)) == 1
