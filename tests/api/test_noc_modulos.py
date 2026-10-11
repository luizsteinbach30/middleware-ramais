"""Módulos aceitos (expansão do NOC, CONTRATO §13.8; ``docs/AGENTE-NOC.md`` item 25).

O que se prova aqui:

- o padrão: ``telefonia`` e ``disponibilidade`` aceitos, CFTV não, e o manifesto traz
  todos os módulos da versão com ``aceito``/``aceitoEm``/``aceitoPor``;
- ``definir_modulos`` (escrita, backup antes, ``LISTA_MUDOU`` com ``atual``,
  ``VALOR_INVALIDO`` para módulo desconhecido) muda o sha do manifesto;
- módulo desligado pausa as verificações dele (sem amostra, sem evento) e religado as
  devolve de ``desconhecido``; ``definir_equipamentos`` recusa verificação nova de
  módulo não aceito e mantém a pausada que não mudou;
- a tela local: só admin troca, com CSRF, e fica ``local:<usuário>``;
- ``telefonia`` é só sinal: desligar não para nada da telefonia.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import UTC, datetime

import pytest

from middleware_monitor.core.models import AmostraDeVerificacao, EventoDeEquipamento, VerificacaoDeEquipamento
from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.equipamentos import lista, modulos
from middleware_monitor.domain.noc import executor, manifesto
from middleware_monitor.integrations.network.base import ResultadoDaSonda
from middleware_monitor.jobs import monitor_equipamentos
from tests.api.test_noc_equipamentos import _definir, _gravador, _para_de, _vista
from tests.api.test_noc_tarefas import _limpo, _processar, _tarefa  # noqa: F401

DEFINIR_MODULOS = "definir_modulos"
ESCRITA = "ESCRITA_REVERSIVEL"
TODOS = ["telefonia", "disponibilidade", "cftv.intelbras", "cftv.hikvision", "cftv.unifi"]


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


def _de(db) -> list[dict]:
    db.expire_all()
    return [{"nome": m["nome"], "aceito": m["aceito"]} for m in modulos.listar(db)]


def _mods(de: list, para: list, id_: str = "m1") -> dict:
    return _tarefa(id_, tipo=DEFINIR_MODULOS, raio=ESCRITA, pedido={"de": de, "para": para})


def _cftv(porta: int = 18081, adaptador: str = "intelbras") -> dict:
    return {
        "id": None,
        "ref": "noc-nvr-cftv",
        "tipo": "cftv",
        "parametros": {"adaptador": adaptador, "porta": porta},
    }


async def _ligar(db, *nomes: str) -> None:
    de = _de(db)
    pronta = await _processar(
        _mods(de, [{"nome": n, "aceito": True} for n in nomes], f"ligar-{uuid.uuid4().hex[:8]}")
    )
    assert pronta.corpo["ok"] is True, pronta.corpo


# --- O padrão e o manifesto -------------------------------------------------------------------


def test_padrao_telefonia_e_disponibilidade_aceitos_e_o_resto_nao(db) -> None:
    lista_de = modulos.listar(db)
    assert [m["nome"] for m in lista_de] == TODOS
    assert {m["nome"]: m["aceito"] for m in lista_de} == {
        "telefonia": True,
        "disponibilidade": True,
        "cftv.intelbras": False,
        "cftv.hikvision": False,
        "cftv.unifi": False,
    }
    assert all(m["aceitoEm"] is None and m["aceitoPor"] is None for m in lista_de)
    homologados = {m["nome"]: m["homologado"] for m in lista_de}
    assert homologados["telefonia"] and homologados["disponibilidade"]
    assert not any(homologados[n] for n in TODOS[2:])
    assert all(m["versao"] == 1 for m in lista_de)


def test_manifesto_leva_os_modulos_na_forma_do_contrato(db) -> None:
    m = manifesto.montar(db)
    assert m["modulos"] == modulos.listar(db)
    assert all(
        set(x) == {"nome", "versao", "homologado", "aceito", "aceitoEm", "aceitoPor"} for x in m["modulos"]
    )
    assert DEFINIR_MODULOS in m["acoes"]
    assert executor.ACOES[DEFINIR_MODULOS].raio == ESCRITA


# --- O verbo ------------------------------------------------------------------------------------


async def test_definir_modulos_liga_grava_quem_e_muda_o_sha_do_manifesto(db, backups) -> None:
    sha_antes = manifesto.montar(db)["sha256"]
    de = _de(db)
    pronta = await _processar(_mods(de, [{"nome": "cftv.intelbras", "aceito": True}]))
    corpo = pronta.corpo
    assert corpo["ok"] is True and corpo["naoSuportado"] is False, corpo
    assert backups == ["noc"]
    r = corpo["resultado"]
    assert set(r) == {"backup", "modulos"}
    por_nome = {m["nome"]: m for m in r["modulos"]}
    assert por_nome["cftv.intelbras"]["aceito"] is True
    assert por_nome["cftv.intelbras"]["aceitoPor"] == "noc:ana@workconnect.com.br"
    assert por_nome["cftv.intelbras"]["aceitoEm"].endswith("Z")
    # O que não mudou não ganha carimbo.
    assert por_nome["disponibilidade"]["aceitoPor"] is None
    db.expire_all()
    assert manifesto.montar(db)["sha256"] != sha_antes
    assert "cftv.intelbras" in modulos.aceitos(db)


async def test_regravar_o_mesmo_valor_nao_muda_o_manifesto(db, backups) -> None:
    sha = manifesto.montar(db)["sha256"]
    pronta = await _processar(_mods(_de(db), [{"nome": "disponibilidade", "aceito": True}]))
    assert pronta.corpo["ok"] is True
    db.expire_all()
    assert manifesto.montar(db)["sha256"] == sha


async def test_de_diferente_do_atual_e_lista_mudou_sem_backup(db, backups) -> None:
    visto = _de(db)
    visto[2]["aceito"] = True  # o NOC acha que cftv.intelbras está ligado
    pronta = await _processar(_mods(visto, [{"nome": "cftv.hikvision", "aceito": True}]))
    corpo = pronta.corpo
    assert corpo["ok"] is False and corpo["naoSuportado"] is True
    assert corpo["resultado"]["recusa"] == "LISTA_MUDOU"
    assert corpo["resultado"]["atual"] == modulos.listar(db)
    assert backups == []
    assert "cftv.hikvision" not in modulos.aceitos(db)


async def test_de_na_forma_do_manifesto_tambem_confere(db, backups) -> None:
    de = manifesto.montar(db)["modulos"]  # com versao, homologado, aceitoEm…
    pronta = await _processar(_mods(de, [{"nome": "cftv.unifi", "aceito": True}]))
    assert pronta.corpo["ok"] is True, pronta.corpo


@pytest.mark.parametrize(
    ("para", "trecho"),
    [
        ([{"nome": "cftv.onvif", "aceito": True}], "desconhecido"),
        ([{"nome": "snmp", "aceito": True}], "desconhecido"),
        ([{"nome": "cftv.intelbras", "aceito": "sim"}], "aceito true ou false"),
        (
            [{"nome": "cftv.intelbras", "aceito": True}, {"nome": "cftv.intelbras", "aceito": False}],
            "repetido",
        ),
    ],
)
async def test_para_invalido_e_valor_invalido(db, backups, para, trecho) -> None:
    pronta = await _processar(_mods(_de(db), para))
    corpo = pronta.corpo
    assert corpo["resultado"]["recusa"] == "VALOR_INVALIDO"
    assert trecho in corpo["erro"]
    assert backups == []


async def test_escrita_repetida_nao_roda_de_novo(db, backups) -> None:
    tarefa = _mods(_de(db), [{"nome": "cftv.intelbras", "aceito": True}], "uma-vez")
    primeira = await _processar(tarefa)
    segunda = await _processar(tarefa)
    assert primeira.corpo == segunda.corpo
    assert backups == ["noc"]


# --- Pausa e retomada ---------------------------------------------------------------------------


async def test_desligar_pausa_sem_amostra_nem_evento_e_religar_volta_de_desconhecido(
    db, backups, monkeypatch
) -> None:
    await _ligar(db, "cftv.intelbras")
    gravador = _gravador()
    gravador["verificacoes"].append(_cftv())
    pronta = await _processar(_definir([], [gravador], "criar"))
    assert pronta.corpo["ok"] is True, pronta.corpo

    rodadas: list[str] = []

    async def sonda(tipo, host, parametros, *, timeout_ping_ms, **_extra):
        rodadas.append(tipo)
        return ResultadoDaSonda(ok=True, valor=5)

    monkeypatch.setattr(monitor_equipamentos.sondas, "executar", sonda)
    await monitor_equipamentos.run_monitor_equipamentos()
    assert sorted(rodadas) == ["cftv", "http", "icmp"]
    db.expire_all()
    cftv_vf = db.query(VerificacaoDeEquipamento).filter_by(tipo="cftv").one()
    assert cftv_vf.estado == "ok"

    # Desliga pelo verbo: a verificação fica guardada e pausada, e o equipamento segue pelas outras.
    pronta = await _processar(_mods(_de(db), [{"nome": "cftv.intelbras", "aceito": False}], "desligar"))
    assert pronta.corpo["ok"] is True
    db.expire_all()
    cftv_vf = db.query(VerificacaoDeEquipamento).filter_by(tipo="cftv").one()
    assert (cftv_vf.estado, cftv_vf.ultima_em, cftv_vf.ultimo_valor) == ("pausada", None, None)
    retrato = lista.retrato(db)[0]
    assert {v["tipo"]: v["estado"] for v in retrato["verificacoes"]}["cftv"] == "pausada"

    amostras = db.query(AmostraDeVerificacao).filter_by(verificacao_id=cftv_vf.id).count()
    eventos = db.query(EventoDeEquipamento).count()
    rodadas.clear()
    # Força todas a vencer: a pausada continua de fora.
    for vf in db.query(VerificacaoDeEquipamento).all():
        vf.ultima_em = None
    db.commit()
    await monitor_equipamentos.run_monitor_equipamentos()
    assert sorted(rodadas) == ["http", "icmp"]
    db.expire_all()
    assert db.query(AmostraDeVerificacao).filter_by(verificacao_id=cftv_vf.id).count() == amostras
    assert db.query(EventoDeEquipamento).count() == eventos

    # Religa: volta de desconhecido e roda na rodada seguinte.
    await _ligar(db, "cftv.intelbras")
    db.expire_all()
    cftv_vf = db.query(VerificacaoDeEquipamento).filter_by(tipo="cftv").one()
    assert cftv_vf.estado == "desconhecido"
    rodadas.clear()
    await monitor_equipamentos.run_monitor_equipamentos()
    assert rodadas == ["cftv"]


async def test_verificacao_cftv_de_modulo_nao_aceito_e_recusada(db, backups) -> None:
    gravador = _gravador()
    gravador["verificacoes"].append(_cftv(adaptador="hikvision"))
    pronta = await _processar(_definir([], [gravador]))
    r = pronta.corpo["resultado"]
    assert r["recusa"] == "VALOR_INVALIDO"
    assert r["motivo"] == "módulo cftv.hikvision não aceito neste agente."
    assert (r["indice"], r["verificacao"]) == (0, 2)
    assert backups == []


async def test_desligar_disponibilidade_recusa_ping_novo(db, backups) -> None:
    await _processar(_mods(_de(db), [{"nome": "disponibilidade", "aceito": False}], "off"))
    pronta = await _processar(_definir([], [_gravador()]))
    assert pronta.corpo["resultado"]["recusa"] == "VALOR_INVALIDO"
    assert "disponibilidade não aceito" in pronta.corpo["resultado"]["motivo"]


async def test_pausada_que_nao_mudou_passa_na_edicao_e_continua_pausada(db, backups) -> None:
    await _ligar(db, "cftv.intelbras")
    gravador = _gravador()
    gravador["verificacoes"].append(_cftv())
    await _processar(_definir([], [gravador], "criar"))
    await _processar(_mods(_de(db), [{"nome": "cftv.intelbras", "aceito": False}], "off"))

    de = _vista(db)
    para = _para_de(de)
    para[0]["nome"] = "NVR renomeado"
    pronta = await _processar(_definir(de, para, "renomear"))
    assert pronta.corpo["ok"] is True, pronta.corpo
    db.expire_all()
    assert db.query(VerificacaoDeEquipamento).filter_by(tipo="cftv").one().estado == "pausada"

    # Mudar a medida (outra porta) de uma pausada é verificação nova: recusada.
    de = _vista(db)
    para = _para_de(de)
    para[0]["verificacoes"][2]["parametros"] = {**para[0]["verificacoes"][2]["parametros"], "porta": 9999}
    pronta = await _processar(_definir(de, para, "mudar-porta"))
    assert pronta.corpo["resultado"]["recusa"] == "VALOR_INVALIDO"


async def test_host_novo_nao_despausa(db, backups) -> None:
    await _ligar(db, "cftv.intelbras")
    gravador = _gravador()
    gravador["verificacoes"].append(_cftv())
    await _processar(_definir([], [gravador], "criar"))
    await _processar(_mods(_de(db), [{"nome": "cftv.intelbras", "aceito": False}], "off"))
    de = _vista(db)
    para = copy.deepcopy(_para_de(de))
    para[0]["host"] = "10.22.8.201"
    pronta = await _processar(_definir(de, para, "host"))
    assert pronta.corpo["ok"] is True, pronta.corpo
    db.expire_all()
    assert db.query(VerificacaoDeEquipamento).filter_by(tipo="cftv").one().estado == "pausada"


# --- A tela local --------------------------------------------------------------------------------


def _login(client, db, *, admin: bool = True) -> str:
    nome = "admin" if admin else "leitor"
    user, senha = bootstrap_admin(db, username=nome, password="SenhaForte123")
    user.must_change_password = False
    if not admin:
        user.role = "viewer"
    db.commit()
    r = client.post("/api/auth/login", json={"username": nome, "password": senha})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def test_tela_local_lista_e_admin_liga_como_local(client, db) -> None:
    csrf = _login(client, db)
    r = client.get("/api/noc/modulos")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["pode_editar"] is True
    assert [m["nome"] for m in corpo["modulos"]] == TODOS
    tel = corpo["modulos"][0]
    assert tel["rotulo"] == "Telefonia" and "não para a coleta" in tel["descricao"]

    r = client.put("/api/noc/modulos/cftv.unifi", json={"aceito": True}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    unifi = {m["nome"]: m for m in r.json()["modulos"]}["cftv.unifi"]
    assert (unifi["aceito"], unifi["aceitoPor"]) == (True, "local:admin")
    db.expire_all()
    assert "cftv.unifi" in modulos.aceitos(db)
    # Vale a última mudança: o manifesto já mostra o que a tela gravou.
    assert {m["nome"]: m["aceitoPor"] for m in manifesto.montar(db)["modulos"]}["cftv.unifi"] == "local:admin"


def test_tela_local_sem_csrf_e_recusada(client, db) -> None:
    _login(client, db)
    r = client.put("/api/noc/modulos/cftv.unifi", json={"aceito": True})
    assert r.status_code == 403
    assert "cftv.unifi" not in modulos.aceitos(db)


def test_tela_local_modulo_desconhecido_e_404(client, db) -> None:
    csrf = _login(client, db)
    r = client.put("/api/noc/modulos/cftv.onvif", json={"aceito": True}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404


def test_tela_local_so_admin_troca(client, db) -> None:
    csrf = _login(client, db, admin=False)
    assert client.get("/api/noc/modulos").json()["pode_editar"] is False
    r = client.put("/api/noc/modulos/cftv.unifi", json={"aceito": True}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403
    assert "cftv.unifi" not in modulos.aceitos(db)


def test_pagina_do_noc_tem_a_secao_modulos(client, db) -> None:
    _login(client, db)
    r = client.get("/system/noc")
    assert r.status_code == 200
    assert 'id="noc-modulos-lista"' in r.text


# --- Telefonia é só sinal --------------------------------------------------------------------------


def test_desligar_telefonia_nao_para_nada_da_telefonia(db) -> None:
    """Regra do dono (30/09): o módulo só vale para o menu. Nenhum job, coleta ou parte
    do lote consulta ``telefonia``; desligar só muda o manifesto."""
    from pathlib import Path

    modulos.definir(db, {"telefonia": False}, por="local:admin")
    db.commit()
    assert "telefonia" not in modulos.aceitos(db)
    assert {m["nome"]: m["aceito"] for m in manifesto.montar(db)["modulos"]}["telefonia"] is False
    raiz = Path(modulos.__file__).resolve().parents[2]
    telefonia = [
        *raiz.joinpath("domain", "mqtt").rglob("*.py"),
        *raiz.joinpath("domain", "extension_configurator").rglob("*.py"),
        *raiz.joinpath("domain", "uscall").rglob("*.py"),
        *raiz.joinpath("domain", "devices").rglob("*.py"),
        *raiz.joinpath("integrations", "extension_configurator").rglob("*.py"),
        *(p for p in raiz.joinpath("jobs").glob("*.py") if p.name != "monitor_equipamentos.py"),
        raiz / "domain" / "noc" / "telemetria.py",
    ]
    assert len(telefonia) > 10
    consulta = re.compile(r"equipamentos(\.| import )modulos|modulos\.aceit")
    quem_consulta = [p.name for p in telefonia if consulta.search(p.read_text(encoding="utf-8"))]
    assert quem_consulta == []
