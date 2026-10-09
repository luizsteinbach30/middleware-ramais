"""CFTV de ponta a ponta no agente (CONTRATO §13.9; ``docs/AGENTE-NOC.md`` itens 23 e 26).

O que se prova aqui, com o simulador Intelbras de verdade em 127.0.0.1:

- a credencial chega pelo ``definir_equipamentos``, é guardada **cifrada**, e o segredo
  **nunca** aparece no retrato, no lote, no resultado da tarefa, no pedido guardado, na
  recusa ou no log — só ``impressao``;
- ``credenciais`` ausente mantém, ``[]`` apaga, credencial nova zera a verificação cftv;
- o job lê o gravador, guarda o ``cftv`` no retrato, gera os eventos ``cftv.*`` só na
  transição (com a verificação) e leva o equipamento a ``degradado`` pela máquina do §13.4;
- "só quando muda": ``lidoEm``, a hora e o desvio abaixo do limite não mudam o hash;
- senha recusada segura a próxima tentativa; sem credencial é falha com o motivo;
- ``verificar_equipamento`` lê agora e não mexe no estado.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from collections.abc import Iterator

import pytest

from middleware_monitor.core.models import (
    CredencialDeEquipamento,
    Equipamento,
    EventoDeEquipamento,
    NocTarefa,
    SystemLog,
    VerificacaoDeEquipamento,
)
from middleware_monitor.domain.equipamentos import cftv, lista, modulos
from middleware_monitor.domain.noc import telemetria
from middleware_monitor.integrations.cftv import unifi
from middleware_monitor.jobs import monitor_equipamentos
from tests.api.test_noc_equipamentos import _definir, _para_de, _vista
from tests.api.test_noc_tarefas import _limpo, _processar, _tarefa  # noqa: F401
from tools.simuladores import intelbras as sim_intelbras

SEGREDO = "simulador123"
SENHA_SECRETA = "Zx9-SenhaQueNaoPodeVazar"


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


@pytest.fixture(autouse=True)
def _caches_limpos() -> Iterator[None]:
    cftv.esquecer_recusas()
    unifi.esquecer_sessoes()
    yield
    cftv.esquecer_recusas()
    unifi.esquecer_sessoes()


@pytest.fixture
def nvr() -> Iterator[sim_intelbras.SimuladorIntelbras]:
    sim = sim_intelbras.SimuladorIntelbras()
    servidor = sim_intelbras.montar_servidor(sim, 0)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    sim.porta = servidor.server_address[1]  # type: ignore[attr-defined]
    yield sim
    servidor.shutdown()
    servidor.server_close()


@pytest.fixture
def aceito(db) -> None:
    modulos.definir(db, {"cftv.intelbras": True}, por="local:teste")
    db.commit()


def _gravador(porta: int, *, segredo: str | None = SEGREDO, **extra) -> dict:
    item = {
        "id": None,
        "ref": "noc-nvr",
        "tipo": "gravador",
        "nome": "NVR Frente de caixa",
        "host": "127.0.0.1",
        "fabricante": "Intelbras",
        "tentativasParaCair": 3,
        "verificacoes": [
            {"id": None, "ref": "noc-nvr-web", "tipo": "tcp", "parametros": {"porta": porta}},
            {
                "id": None,
                "ref": "noc-nvr-cftv",
                "tipo": "cftv",
                "parametros": {"adaptador": "intelbras", "porta": porta},
            },
        ],
        **extra,
    }
    if segredo is not None and "credenciais" not in extra:
        item["credenciais"] = [{"tipo": "HTTP", "usuario": "admin", "segredo": segredo}]
    return item


async def _criar(db, porta: int, **extra) -> dict:
    pronta = await _processar(_definir([], [_gravador(porta, **extra)], f"criar-{uuid.uuid4().hex[:6]}"))
    assert pronta.corpo["ok"] is True, pronta.corpo
    return pronta.corpo


async def _rodar(db) -> None:
    """Uma rodada do job com tudo vencido."""
    for vf in db.query(VerificacaoDeEquipamento).all():
        vf.ultima_em = None
    db.commit()
    await monitor_equipamentos.run_monitor_equipamentos()
    db.expire_all()


def _vf_cftv(db) -> VerificacaoDeEquipamento:
    return db.query(VerificacaoDeEquipamento).filter_by(tipo="cftv").one()


def _eventos(db, prefixo: str = "") -> list[EventoDeEquipamento]:
    return [
        e
        for e in db.query(EventoDeEquipamento).order_by(EventoDeEquipamento.id)
        if e.tipo.startswith(prefixo)
    ]


# --- Credencial --------------------------------------------------------------------------------------


async def test_credencial_cifrada_e_so_a_impressao_sai(db, backups, nvr, aceito, capfd) -> None:
    corpo = await _criar(db, nvr.porta, segredo=SENHA_SECRETA)
    linha = db.query(CredencialDeEquipamento).one()
    assert linha.segredo.startswith("enc:v1:") and SENHA_SECRETA not in linha.segredo
    impressao = hashlib.sha256(SENHA_SECRETA.encode()).hexdigest()[:12]
    assert linha.impressao == impressao

    retrato = lista.retrato(db)[0]
    assert retrato["credenciais"] == [{"tipo": "HTTP", "usuario": "admin", "impressao": impressao}]

    # O segredo em lugar nenhum que sai daqui ou fica em claro.
    await _rodar(db)
    lote, _cur, _mais = telemetria.montar_lote(db, telemetria.cursores(db))
    lugares = {
        "resultado": json.dumps(corpo),
        "lote": json.dumps(lote),
        "pedido guardado": " ".join(t.pedido for t in db.query(NocTarefa).all()),
        "resultado guardado": " ".join(t.resultado or "" for t in db.query(NocTarefa).all()),
        "log do sistema": " ".join(f"{r.message} {r.context}" for r in db.query(SystemLog).all()),
        "saída do processo": "".join(capfd.readouterr()),
    }
    for onde, texto in lugares.items():
        assert SENHA_SECRETA not in texto, onde
    assert '"segredo": "***"' in lugares["pedido guardado"]


async def test_de_nao_leva_credencial_e_ausente_mantem(db, backups, nvr, aceito) -> None:
    await _criar(db, nvr.porta)
    de = _vista(db)
    assert "credenciais" not in de[0]
    visto = copy.deepcopy(de)
    visto[0]["credenciais"] = [{"tipo": "HTTP", "usuario": "x", "segredo": "y"}]  # ignorado no de
    para = _para_de(de)
    para[0]["nome"] = "NVR renomeado"  # sem credenciais: mantém
    pronta = await _processar(_definir(visto, para, "renomear"))
    assert pronta.corpo["ok"] is True, pronta.corpo
    db.expire_all()
    assert db.query(CredencialDeEquipamento).count() == 1

    para = _para_de(_vista(db))
    para[0]["credenciais"] = []
    pronta = await _processar(_definir(_vista(db), para, "apagar"))
    assert pronta.corpo["ok"] is True
    db.expire_all()
    assert db.query(CredencialDeEquipamento).count() == 0
    assert lista.retrato(db)[0]["credenciais"] == []


async def test_credencial_nova_zera_a_verificacao_cftv(db, backups, nvr, aceito) -> None:
    await _criar(db, nvr.porta, segredo="errada")
    await _rodar(db)
    vf = _vf_cftv(db)
    assert vf.ultimo_erro == "senha recusada" and vf.falhas_seguidas == 1

    para = _para_de(_vista(db))
    para[0]["credenciais"] = [{"tipo": "HTTP", "usuario": "admin", "segredo": SEGREDO}]
    pronta = await _processar(_definir(_vista(db), para, "trocar"))
    assert pronta.corpo["ok"] is True
    db.expire_all()
    vf = _vf_cftv(db)
    assert (vf.estado, vf.falhas_seguidas, vf.ultimo_erro, vf.ultima_em) == ("desconhecido", 0, None, None)
    # A outra verificação não mediu credencial: não é zerada.
    tcp = db.query(VerificacaoDeEquipamento).filter_by(tipo="tcp").one()
    assert tcp.estado == "ok"


@pytest.mark.parametrize(
    ("credenciais", "trecho"),
    [
        ([{"tipo": "TELNET", "usuario": "a", "segredo": "b"}], "fora da lista"),
        ([{"tipo": "HTTP", "usuario": "a", "segredo": ""}], "segredo é texto"),
        (
            [{"tipo": "HTTP", "usuario": "a", "segredo": SENHA_SECRETA, "porta": 80}],
            "{ tipo, usuario, segredo }",
        ),
        (
            [
                {"tipo": "HTTP", "usuario": "a", "segredo": SENHA_SECRETA},
                {"tipo": "HTTP", "usuario": "b", "segredo": SENHA_SECRETA},
            ],
            "repetida",
        ),
        ("admin:senha", "lista"),
    ],
)
async def test_credencial_fora_de_forma_e_recusada_sem_ecoar_o_segredo(
    db, backups, nvr, aceito, credenciais, trecho
) -> None:
    pronta = await _processar(_definir([], [_gravador(nvr.porta, credenciais=credenciais)]))
    r = pronta.corpo["resultado"]
    assert r["recusa"] == "VALOR_INVALIDO" and trecho in r["motivo"], r
    assert SENHA_SECRETA not in json.dumps(pronta.corpo)
    assert backups == []


async def test_cftv_so_em_gravador_uma_por_equipamento_e_porta_padrao(db, backups, aceito) -> None:
    camera = _gravador(80, tipo="camera")
    pronta = await _processar(_definir([], [camera], "camera"))
    assert "só em equipamento gravador" in pronta.corpo["resultado"]["motivo"]

    duas = _gravador(80)
    duas["verificacoes"].append(
        {"id": None, "ref": "outra", "tipo": "cftv", "parametros": {"adaptador": "intelbras"}}
    )
    pronta = await _processar(_definir([], [duas], "duas"))
    assert "uma verificação cftv" in pronta.corpo["resultado"]["motivo"]

    item = _gravador(80)
    item["verificacoes"][1]["parametros"] = {"adaptador": "intelbras", "esquema": "https"}
    pronta = await _processar(_definir([], [item], "https"))
    assert pronta.corpo["ok"] is True, pronta.corpo
    cftv_vf = next(v for v in _vista(db)[0]["verificacoes"] if v["tipo"] == "cftv")
    assert cftv_vf["parametros"] == {"adaptador": "intelbras", "esquema": "https", "porta": 443}


# --- A leitura, os eventos e o estado --------------------------------------------------------------------


async def test_le_o_gravador_e_leva_o_cftv_no_retrato(db, backups, nvr, aceito) -> None:
    await _criar(db, nvr.porta)
    await _rodar(db)
    vf = _vf_cftv(db)
    assert (vf.estado, vf.ultimo_erro) == ("ok", None)
    assert vf.ultimo_valor is not None
    eq = lista.retrato(db)[0]
    assert eq["estado"] == "no_ar"
    c = eq["cftv"]
    assert set(c) == {"lidoEm", "canais", "discos", "horaDoGravador", "desvioS", "diasGuardados"}
    assert c["canais"][0] == {"numero": 1, "nome": "CAM 1", "video": "ok", "gravando": True}
    assert c["discos"] == [{"numero": 1, "estado": "ok", "totalGb": 2000.0, "livreGb": 800.0}]
    assert abs(c["desvioS"]) <= 5 and c["diasGuardados"] is None
    assert _eventos(db, "cftv.") == []  # primeira leitura sem problema: nada é notícia
    # Só leitura: o simulador só viu GET.
    assert {m for m, _ in nvr.pedidos} == {"GET"}


async def test_canal_sem_video_gera_evento_na_hora_e_degrada_depois_das_tentativas(
    db, backups, nvr, aceito
) -> None:
    await _criar(db, nvr.porta)
    await _rodar(db)
    nvr.mudar({"semVideo": [2]})
    await _rodar(db)
    ev = _eventos(db, "cftv.")
    assert [(e.tipo, e.de, e.para, e.detalhe) for e in ev] == [
        ("cftv.canal_sem_video", "ok", "sem_video", "canal 2 (CAM 2) sem vídeo")
    ]
    vf = _vf_cftv(db)
    assert ev[0].verificacao_id == vf.id
    assert (vf.estado, vf.falhas_seguidas, vf.ultimo_erro) == ("ok", 1, "canal 2 sem vídeo")
    eq = db.query(Equipamento).one()
    assert eq.estado == "no_ar"

    # Repetição não é evento; a terceira falha seguida degrada o equipamento.
    await _rodar(db)
    await _rodar(db)
    assert len(_eventos(db, "cftv.")) == 1
    eq = db.query(Equipamento).one()
    assert eq.estado == "degradado"
    degradou = _eventos(db, "equipamento.")
    assert [e.tipo for e in degradou] == ["equipamento.degradou"]
    assert degradou[0].verificacao_id == vf.id

    # Volta: evento do canal e normalização.
    nvr.mudar({"semVideo": []})
    await _rodar(db)
    assert [e.tipo for e in _eventos(db)][-2:] == ["cftv.canal_voltou", "equipamento.normalizou"]
    assert db.query(Equipamento).one().estado == "no_ar"

    # O lote leva os eventos com a verificação.
    lote, _c, _m = telemetria.montar_lote(db, {**telemetria.cursores(db), "eventosEquipamento": 0})
    tipos = [(e["tipo"], e["verificacao"]) for e in lote["eventosDeEquipamento"]]
    assert ("cftv.canal_sem_video", vf.id) in tipos


async def test_disco_gravacao_e_hora_viram_eventos(db, backups, nvr, aceito) -> None:
    await _criar(db, nvr.porta)
    await _rodar(db)
    nvr.mudar(
        {
            "discos": [{"estado": "defeito", "totalGb": 2000, "livreGb": 800}],
            "naoGravando": [4],
            "desvioS": -600,
        }
    )
    await _rodar(db)
    ev = {e.tipo: e for e in _eventos(db, "cftv.")}
    assert set(ev) == {"cftv.disco_com_defeito", "cftv.parou_de_gravar", "cftv.hora_errada"}
    assert (ev["cftv.disco_com_defeito"].de, ev["cftv.disco_com_defeito"].para) == ("ok", "defeito")
    assert ev["cftv.disco_com_defeito"].detalhe == "disco 1 com defeito"
    assert ev["cftv.parou_de_gravar"].detalhe == "canal 4 (CAM 4) parou de gravar"
    assert (ev["cftv.hora_errada"].de, ev["cftv.hora_errada"].para) == ("certa", "errada")
    assert "atrasada" in ev["cftv.hora_errada"].detalhe
    erro = _vf_cftv(db).ultimo_erro or ""
    assert "canal 4 não está gravando" in erro and "disco 1 com defeito" in erro and "atrasada" in erro

    nvr.mudar(
        {"discos": [{"estado": "cheio", "totalGb": 2000, "livreGb": 0}], "naoGravando": [], "desvioS": 0}
    )
    await _rodar(db)
    novos = [(e.tipo, e.de, e.para) for e in _eventos(db, "cftv.")[3:]]
    assert sorted(novos) == [
        ("cftv.disco_com_defeito", "defeito", "cheio"),
        ("cftv.hora_certa", "errada", "certa"),
        ("cftv.voltou_a_gravar", "parado", "gravando"),
    ]


async def test_primeira_leitura_com_problema_ja_e_evento(db, backups, nvr, aceito) -> None:
    nvr.mudar({"semVideo": [3]})
    await _criar(db, nvr.porta)
    await _rodar(db)
    assert [(e.tipo, e.de) for e in _eventos(db, "cftv.")] == [("cftv.canal_sem_video", "desconhecido")]


async def test_resposta_vazia_e_falha_e_nao_apaga_a_leitura_anterior(db, backups, nvr, aceito) -> None:
    await _criar(db, nvr.porta)
    await _rodar(db)
    antes = lista.retrato(db)[0]["cftv"]
    nvr.mudar({"resposta": "vazia", "semVideo": [1]})
    await _rodar(db)
    vf = _vf_cftv(db)
    assert "resposta vazia" in (vf.ultimo_erro or "")
    assert lista.retrato(db)[0]["cftv"] == antes
    assert _eventos(db, "cftv.") == []


async def test_senha_recusada_segura_a_proxima_tentativa(db, backups, nvr, aceito) -> None:
    await _criar(db, nvr.porta, segredo="errada")
    await _rodar(db)
    assert _vf_cftv(db).ultimo_erro == "senha recusada"
    pedidos = len(nvr.pedidos)
    await _rodar(db)
    vf = _vf_cftv(db)
    assert "segurada" in (vf.ultimo_erro or "") and vf.falhas_seguidas == 2
    # Só o tcp foi ao gravador; a leitura com a senha errada não.
    assert len(nvr.pedidos) == pedidos


async def test_sem_credencial_e_falha_com_o_motivo(db, backups, nvr, aceito) -> None:
    await _criar(db, nvr.porta, segredo=None)
    await _rodar(db)
    assert _vf_cftv(db).ultimo_erro == "sem credencial (HTTP)"
    assert nvr.pedidos == []


async def test_verificar_equipamento_le_agora_sem_mexer_no_estado(db, backups, nvr, aceito) -> None:
    corpo = await _criar(db, nvr.porta)
    eid = corpo["resultado"]["equipamentos"][0]["id"]
    nvr.mudar({"semVideo": [2]})
    pronta = await _processar(_tarefa("v1", tipo="verificar_equipamento", pedido={"equipamento": eid}))
    r = pronta.corpo["resultado"]
    cftv_r = next(v for v in r["verificacoes"] if v["tipo"] == "cftv")
    assert cftv_r["ok"] is False and cftv_r["erro"] == "canal 2 sem vídeo"
    assert cftv_r["cftv"]["canais"][1]["video"] == "sem_video"
    db.expire_all()
    vf = _vf_cftv(db)
    assert (vf.estado, vf.ultima_em) == ("desconhecido", None)
    assert _eventos(db) == []


# --- Só quando muda ------------------------------------------------------------------------------------


def test_hash_ignora_hora_lido_em_e_desvio_abaixo_do_limite() -> None:
    base = {
        "id": "eq_1",
        "verificacoes": [],
        "cftv": {
            "lidoEm": "2026-09-30T10:00:00",
            "canais": [{"numero": 1, "nome": "CAM 1", "video": "ok", "gravando": True}],
            "discos": [{"numero": 1, "estado": "ok", "totalGb": 2000.0, "livreGb": 810.0}],
            "horaDoGravador": "2026-09-30T10:00:03",
            "desvioS": 3,
            "diasGuardados": None,
        },
    }
    sha = lista.sha256_do_retrato([base])

    def com(**mudanca) -> str:
        outro = copy.deepcopy(base)
        outro["cftv"].update(mudanca)
        return lista.sha256_do_retrato([outro])

    assert com(lidoEm="2026-09-30T10:01:00", horaDoGravador="2026-09-30T10:01:10", desvioS=10) == sha
    assert com(desvioS=-119) == sha
    assert com(desvioS=121) != sha
    # O livre anda em faixas de 1% do total: gravar não muda o retrato a cada leitura.
    assert com(discos=[{"numero": 1, "estado": "ok", "totalGb": 2000.0, "livreGb": 805.0}]) == sha
    assert com(discos=[{"numero": 1, "estado": "ok", "totalGb": 2000.0, "livreGb": 700.0}]) != sha
    assert com(discos=[{"numero": 1, "estado": "defeito", "totalGb": 2000.0, "livreGb": 810.0}]) != sha
    assert com(canais=[{"numero": 1, "nome": "CAM 1", "video": "sem_video", "gravando": True}]) != sha
