"""Equipamentos pelo NOC (expansão, Plano 0) — ``docs/AGENTE-NOC.md`` itens 18 a 21 e
``noc-workconnect/docs/CONTRATO-DO-AGENTE.md`` §13.

O que se prova aqui:

- ``definir_equipamentos`` cria (com ``paiRef`` de um pai que nasce no mesmo pedido),
  altera mantendo a identidade, remove; confere o ``de`` como conjunto por ``id`` e recusa
  a lista que mudou (``LISTA_MUDOU``, com a atual e sem backup); valida o ``para``
  (``VALOR_INVALIDO`` com índice e motivo); faz backup antes e não grava sem ele;
- ``verificar_equipamento`` roda as verificações agora e **não** mexe no estado do job;
- o lote leva ``amostrasDeVerificacao`` e ``eventosDeEquipamento`` por cursor, que só anda
  com 202, e ``equipamentos`` só quando muda;
- o manifesto declara ``modulos``.
"""

from __future__ import annotations

import copy
import socket
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from middleware_monitor.core.models import (
    AmostraDeVerificacao,
    Equipamento,
    EventoDeEquipamento,
    VerificacaoDeEquipamento,
)
from middleware_monitor.domain.backup import snapshot
from middleware_monitor.domain.equipamentos import lista
from middleware_monitor.domain.noc import estado, executor, manifesto, telemetria
from middleware_monitor.jobs.noc_agent import run_noc_telemetria
from tests.api.test_noc_agent import AutoridadeDeTeste, _enrolado_direto
from tests.api.test_noc_tarefas import _limpo, _processar, _tarefa  # noqa: F401
from tests.api.test_noc_telemetria import URL, _corpo

DEFINIR = "definir_equipamentos"
VERIFICAR = "verificar_equipamento"
ESCRITA = "ESCRITA_REVERSIVEL"


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _gravador(**extra) -> dict:
    return {
        "id": None,
        "ref": "noc-nvr",
        "tipo": "gravador",
        "nome": "NVR Frente de caixa",
        "host": "10.22.8.200",
        "fabricante": "Intelbras",
        "modelo": "NVD 1432",
        "verificacoes": [
            {"id": None, "ref": "noc-nvr-ping", "tipo": "icmp", "parametros": {}},
            {
                "id": None,
                "ref": "noc-nvr-web",
                "tipo": "http",
                "parametros": {"caminho": "/"},
                "intervaloS": 120,
            },
        ],
        **extra,
    }


def _camera(**extra) -> dict:
    return {
        "id": None,
        "ref": "noc-cam-1",
        "tipo": "camera",
        "nome": "Câmera caixa 1",
        "host": "10.22.8.31",
        "paiRef": "noc-nvr",
        "verificacoes": [{"id": None, "ref": "noc-cam-1-rtsp", "tipo": "tcp", "parametros": {"porta": 554}}],
        **extra,
    }


def _definir(de: list, para: list, id_: str = "d1") -> dict:
    return _tarefa(id_, tipo=DEFINIR, raio=ESCRITA, pedido={"de": de, "para": para})


def _vista(db) -> list[dict]:
    db.expire_all()
    return lista.vista(db)


def _para_de(de: list[dict]) -> list[dict]:
    """O ``para`` que mantém tudo como está: cada item de ``de`` com ``paiRef`` no lugar de ``paiId``."""
    refs = {eq["id"]: eq["ref"] for eq in de}
    saida = []
    for eq in copy.deepcopy(de):
        pai = eq.pop("paiId")
        eq["paiRef"] = refs.get(pai) if pai else None
        saida.append(eq)
    return saida


@pytest.fixture
def backups(monkeypatch) -> list[str]:
    """Conta os snapshots sem tirar um de verdade."""
    feitos: list[str] = []

    class _Arquivo:
        name = "snapshot-noc.db"

    def criar(**_k):
        feitos.append("noc")
        return _Arquivo()

    monkeypatch.setattr(snapshot, "create_snapshot", criar)
    return feitos


async def _criar_dois(db) -> list[dict]:
    pronta = await _processar(_definir([], [_gravador(), _camera()], "criar"))
    assert pronta.corpo["ok"] is True, pronta.corpo
    return _vista(db)


# --- A forma ------------------------------------------------------------------------------------


def test_os_verbos_e_os_raios() -> None:
    assert executor.ACOES[DEFINIR].raio == ESCRITA
    assert executor.ACOES[VERIFICAR].raio == "LEITURA"
    falta = executor.conferir(DEFINIR, ESCRITA, {"de": []})
    assert falta is not None and "precisa de: para" in (falta.erro or "")
    rede = executor.conferir(DEFINIR, ESCRITA, {"de": [], "para": [], "gateway": "10.0.0.1"})
    assert rede is not None and "P_CODE_DE_REDE" in (rede.erro or "")


def test_manifesto_declara_o_modulo_disponibilidade(db) -> None:
    m = manifesto.montar(db)
    # Plano 1 (CONTRATO §13.8): todos os módulos da versão, com o aceite deste agente.
    assert {x["nome"]: x for x in m["modulos"]}["disponibilidade"] == {
        "nome": "disponibilidade",
        "versao": 1,
        "homologado": True,
        "aceito": True,
        "aceitoEm": None,
        "aceitoPor": None,
    }
    assert {DEFINIR, VERIFICAR} <= set(m["acoes"])


# --- definir_equipamentos -----------------------------------------------------------------------


async def test_cria_com_pai_novo_no_mesmo_pedido_e_devolve_os_ids(db, backups) -> None:
    pronta = await _processar(_definir([], [_camera(), _gravador()]))
    corpo = pronta.corpo
    assert corpo["ok"] is True and corpo["naoSuportado"] is False, corpo
    r = corpo["resultado"]
    assert r["backup"] == "snapshot-noc.db" and backups == ["noc"]
    assert (r["criados"], r["removidos"], r["alterados"]) == (2, 0, 0)
    por_ref = {e["ref"]: e for e in r["equipamentos"]}
    assert [e["ref"] for e in r["equipamentos"]] == ["noc-cam-1", "noc-nvr"]
    nvr, cam = por_ref["noc-nvr"], por_ref["noc-cam-1"]
    assert nvr["id"].startswith("eq_") and len(nvr["id"]) == 19
    assert [v["ref"] for v in nvr["verificacoes"]] == ["noc-nvr-ping", "noc-nvr-web"]
    assert all(v["id"].startswith("vf_") and len(v["id"]) == 15 for v in nvr["verificacoes"])

    vista = {e["ref"]: e for e in _vista(db)}
    assert vista["noc-cam-1"]["paiId"] == nvr["id"]
    assert vista["noc-cam-1"]["id"] == cam["id"]
    assert vista["noc-nvr"]["tentativasParaCair"] == 3
    # Os padrões preenchidos: http na 80, status 200, sem conferir certificado; intervalo 60.
    web = vista["noc-nvr"]["verificacoes"][1]
    assert web["parametros"] == {
        "esquema": "http",
        "porta": 80,
        "caminho": "/",
        "statusEsperado": 200,
        "verificarCertificado": False,
    }
    assert web["intervaloS"] == 120
    assert vista["noc-nvr"]["verificacoes"][0]["intervaloS"] == 60
    eq = db.get(Equipamento, nvr["id"])
    assert eq is not None and eq.estado == "desconhecido"


async def test_altera_mantendo_a_identidade_e_zera_so_o_que_mudou_de_medida(db, backups) -> None:
    de = await _criar_dois(db)
    nvr = next(e for e in de if e["ref"] == "noc-nvr")
    ids_antes = {e["ref"]: e["id"] for e in de}
    # Estado de uma leitura anterior, para ver o que é zerado.
    for vf in db.query(VerificacaoDeEquipamento).all():
        vf.estado, vf.ultimo_valor, vf.ultima_em = "ok", 9, _agora()
    db.commit()

    para = _para_de(de)
    alvo = next(e for e in para if e["ref"] == "noc-nvr")
    alvo["nome"] = "NVR renomeado"
    alvo["verificacoes"][1]["parametros"] = {**alvo["verificacoes"][1]["parametros"], "porta": 8080}
    pronta = await _processar(_definir(de, para))
    r = pronta.corpo["resultado"]
    assert pronta.corpo["ok"] is True, pronta.corpo
    assert (r["criados"], r["removidos"], r["alterados"]) == (0, 0, 1)
    assert {e["ref"]: e["id"] for e in r["equipamentos"]} == ids_antes

    db.expire_all()
    eq = db.get(Equipamento, nvr["id"])
    assert eq is not None and eq.nome == "NVR renomeado"
    ping, web = eq.verificacoes
    assert (ping.estado, ping.ultimo_valor) == ("ok", 9)  # a medida não mudou
    assert (web.estado, web.ultimo_valor, web.ultima_em) == ("desconhecido", None, None)
    assert ping.id == nvr["verificacoes"][0]["id"] and web.id == nvr["verificacoes"][1]["id"]


async def test_remove_o_que_nao_esta_no_para(db, backups) -> None:
    de = await _criar_dois(db)
    cam = next(e for e in de if e["ref"] == "noc-cam-1")
    para = [e for e in _para_de(de) if e["ref"] == "noc-nvr"]
    # E acrescenta uma verificação nova no que fica.
    para[0]["verificacoes"].append(
        {"id": None, "ref": "noc-nvr-ssh", "tipo": "tcp", "parametros": {"porta": 22}}
    )
    pronta = await _processar(_definir(de, para))
    r = pronta.corpo["resultado"]
    assert (r["criados"], r["removidos"], r["alterados"]) == (0, 1, 1)
    db.expire_all()
    assert db.get(Equipamento, cam["id"]) is None
    assert db.query(VerificacaoDeEquipamento).filter_by(equipamento_id=cam["id"]).count() == 0
    assert [v["ref"] for v in r["equipamentos"][0]["verificacoes"]] == [
        "noc-nvr-ping",
        "noc-nvr-web",
        "noc-nvr-ssh",
    ]


async def test_lista_vazia_remove_tudo(db, backups) -> None:
    de = await _criar_dois(db)
    pronta = await _processar(_definir(de, []))
    assert pronta.corpo["ok"] is True
    assert pronta.corpo["resultado"]["removidos"] == 2
    assert _vista(db) == []


async def test_de_confere_como_conjunto_ausente_igual_a_null(db, backups) -> None:
    de = await _criar_dois(db)
    embaralhado = copy.deepcopy(list(reversed(de)))
    for eq in embaralhado:
        eq.pop("mac")  # ausente = null
        eq["verificacoes"].reverse()
        eq["estado"] = "no_ar"  # campo do retrato que o de não compara
    pronta = await _processar(_definir(embaralhado, _para_de(de)))
    assert pronta.corpo["ok"] is True, pronta.corpo
    assert pronta.corpo["resultado"]["alterados"] == 0


@pytest.mark.parametrize(
    "mexer",
    [
        pytest.param(lambda de: de[0].update(nome="outro nome"), id="campo"),
        pytest.param(lambda de: de.pop(), id="faltando"),
        pytest.param(lambda de: de.append({**de[0], "id": "eq_0000000000000000"}), id="sobrando"),
        pytest.param(lambda de: de[0]["verificacoes"].pop(), id="verificacao"),
        pytest.param(lambda de: de[0].update(tentativasParaCair=True), id="true-nao-e-1"),
    ],
)
async def test_lista_que_mudou_e_recusada_sem_backup_e_sem_gravar(db, backups, mexer) -> None:
    atual = await _criar_dois(db)
    backups.clear()
    visto = copy.deepcopy(atual)
    mexer(visto)
    para = _para_de(atual)
    para[0]["nome"] = "nunca gravado"
    pronta = await _processar(_definir(visto, para, "stale"))
    corpo = pronta.corpo
    assert corpo["ok"] is False and corpo["naoSuportado"] is True
    assert corpo["resultado"]["recusa"] == "LISTA_MUDOU"
    assert corpo["resultado"]["atual"] == atual
    assert backups == []
    assert _vista(db) == atual


@pytest.mark.parametrize(
    ("mudanca", "indice", "trecho"),
    [
        ({"tipo": "geladeira"}, 0, "tipo 'geladeira' fora da lista"),
        ({"host": ""}, 0, "host é obrigatório"),
        ({"host": "http://10.0.0.1/"}, 0, "não é um IP nem um nome"),
        ({"verificacoes": [{"id": None, "ref": "x", "tipo": "tcp", "parametros": {"porta": 0}}]}, 0, "porta"),
        (
            {"verificacoes": [{"id": None, "ref": "x", "tipo": "tcp", "parametros": {"porta": 70000}}]},
            0,
            "porta",
        ),
        ({"verificacoes": [{"id": None, "ref": "x", "tipo": "icmp", "intervaloS": 29}]}, 0, "intervaloS"),
        (
            {"verificacoes": [{"id": None, "ref": f"v{i}", "tipo": "icmp"} for i in range(11)]},
            0,
            "no máximo 10",
        ),
        ({"verificacoes": [{"id": None, "ref": "x", "tipo": "snmp"}]}, 0, "fora da lista"),
        (
            {
                "verificacoes": [
                    {"id": None, "ref": "x", "tipo": "icmp", "parametros": {"comunidade": "public"}}
                ]
            },
            0,
            "não aceita",
        ),
        ({"paiRef": "noc-nao-existe"}, 0, "paiRef 'noc-nao-existe'"),
        ({"ref": "noc-cam-1"}, 1, "ref repetido"),
        ({"paiId": "eq_0000000000000000"}, 0, "paiRef"),
        ({"id": "eq_0000000000000000"}, 0, "não existe"),
        ({"tentativasParaCair": 0}, 0, "tentativasParaCair"),
    ],
)
async def test_para_invalido_e_recusado_com_indice_e_motivo_antes_do_backup(
    db, backups, mudanca, indice, trecho
) -> None:
    alvo = _gravador(**mudanca)
    para = [_camera(paiRef=None), alvo] if indice == 1 else [alvo, _camera(paiRef=None)]
    pronta = await _processar(_definir([], para))
    corpo = pronta.corpo
    assert corpo["ok"] is False and corpo["naoSuportado"] is True
    r = corpo["resultado"]
    assert r["recusa"] == "VALOR_INVALIDO"
    assert r["indice"] == indice
    assert trecho in r["motivo"], r["motivo"]
    assert backups == []
    assert _vista(db) == []


async def test_pai_que_fecha_ciclo_e_recusado(db, backups) -> None:
    a = _gravador(paiRef="noc-cam-1")
    b = _camera(paiRef="noc-nvr")
    pronta = await _processar(_definir([], [a, b]))
    assert pronta.corpo["resultado"]["recusa"] == "VALOR_INVALIDO"
    assert "ciclo" in pronta.corpo["resultado"]["motivo"]


async def test_verificacao_de_outro_equipamento_e_recusada(db, backups) -> None:
    de = await _criar_dois(db)
    para = _para_de(de)
    nvr = next(e for e in para if e["ref"] == "noc-nvr")
    cam = next(e for e in para if e["ref"] == "noc-cam-1")
    nvr["verificacoes"].append(cam["verificacoes"][0])
    pronta = await _processar(_definir(de, para))
    r = pronta.corpo["resultado"]
    assert r["recusa"] == "VALOR_INVALIDO" and "de outro equipamento" in r["motivo"]
    assert r["verificacao"] == 2


async def test_sem_backup_nada_e_gravado(db, monkeypatch) -> None:
    def quebra(**_k):
        raise snapshot.SnapshotError("disco cheio")

    monkeypatch.setattr(snapshot, "create_snapshot", quebra)
    pronta = await _processar(_definir([], [_gravador()]))
    assert pronta.corpo["ok"] is False
    assert "Backup obrigatório falhou" in (pronta.corpo["erro"] or "")
    assert _vista(db) == []


async def test_lista_que_muda_durante_o_backup_e_recusada_na_transacao(db, monkeypatch) -> None:
    class _Arquivo:
        name = "snap.db"

    def backup_que_deixa_a_loja_mexer(**_k):
        with executor.session_factory() as outra:
            validado = lista.validar_para([_camera(paiRef=None)], lista.vista(outra))
            lista.gravar(outra, validado, _agora())
            outra.commit()
        return _Arquivo()

    monkeypatch.setattr(snapshot, "create_snapshot", backup_que_deixa_a_loja_mexer)
    pronta = await _processar(_definir([], [_gravador()]))
    assert pronta.corpo["resultado"]["recusa"] == "LISTA_MUDOU"
    assert [e["ref"] for e in _vista(db)] == ["noc-cam-1"]


async def test_escrita_repetida_nao_roda_de_novo(db, backups) -> None:
    tarefa = _definir([], [_gravador()], "uma-vez")
    primeira = await _processar(tarefa)
    segunda = await _processar(tarefa)
    assert primeira.corpo == segunda.corpo
    assert backups == ["noc"]
    assert len(_vista(db)) == 1


# --- verificar_equipamento ----------------------------------------------------------------------


async def test_verificar_roda_agora_e_nao_mexe_no_estado(db, backups) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    porta = s.getsockname()[1]
    try:
        eq = {
            "id": None,
            "ref": "srv",
            "tipo": "servidor",
            "nome": "Servidor local",
            "host": "127.0.0.1",
            "verificacoes": [{"id": None, "ref": "ssh", "tipo": "tcp", "parametros": {"porta": porta}}],
        }
        await _processar(_definir([], [eq]))
        [cadastrado] = _vista(db)
        vf = db.get(VerificacaoDeEquipamento, cadastrado["verificacoes"][0]["id"])
        assert vf is not None
        vf.estado, vf.falhas_seguidas = "ok", 2
        db.commit()

        pronta = await _processar(
            _tarefa("v1", tipo=VERIFICAR, raio="LEITURA", pedido={"equipamento": cadastrado["id"]})
        )
    finally:
        s.close()
    corpo = pronta.corpo
    assert corpo["ok"] is True, corpo
    r = corpo["resultado"]
    assert r["equipamento"] == cadastrado["id"] and r["estado"] == "desconhecido"
    [leitura] = r["verificacoes"]
    assert leitura["id"] == vf.id and leitura["tipo"] == "tcp"
    assert leitura["ok"] is True and isinstance(leitura["valor"], int) and leitura["erro"] is None
    assert "T" in leitura["em"] and not leitura["em"].endswith("Z")
    # Leitura avulsa: nada do job mudou, e não nasceu amostra nem evento.
    db.expire_all()
    vf = db.get(VerificacaoDeEquipamento, vf.id)
    assert vf is not None and (vf.estado, vf.falhas_seguidas, vf.ultima_em) == ("ok", 2, None)
    assert db.query(AmostraDeVerificacao).count() == 0
    assert db.query(EventoDeEquipamento).count() == 0


async def test_verificar_equipamento_que_nao_existe(db) -> None:
    pronta = await _processar(
        _tarefa("v2", tipo=VERIFICAR, raio="LEITURA", pedido={"equipamento": "eq_ffffffffffffffff"})
    )
    assert pronta.corpo["ok"] is False
    assert pronta.corpo["resultado"]["recusa"] == "EQUIPAMENTO_NAO_ENCONTRADO"


# --- O lote -------------------------------------------------------------------------------------


async def _com_leituras(db) -> list[dict]:
    de = await _criar_dois(db)
    vfs = [v["id"] for eq in de for v in eq["verificacoes"]]
    agora = _agora()
    for i, vid in enumerate(vfs):
        db.add(
            AmostraDeVerificacao(
                verificacao_id=vid, em=agora, ok=i % 2 == 0, valor=10 + i if i % 2 == 0 else None
            )
        )
    db.add(
        EventoDeEquipamento(
            equipamento_id=de[0]["id"],
            verificacao_id=de[0]["verificacoes"][0]["id"],
            tipo="equipamento.caiu",
            de="no_ar",
            para="fora",
            em=agora,
            detalhe="icmp: sem sucesso em 3 tentativas seguidas — sem resposta",
        )
    )
    db.commit()
    return de


@respx.mock
async def test_lote_leva_amostras_e_eventos_por_cursor_e_o_retrato_so_quando_muda(db, backups) -> None:
    _enrolado_direto(db, AutoridadeDeTeste())
    de = await _com_leituras(db)
    rota = respx.post(URL).mock(return_value=httpx.Response(503, json={"mensagem": "fora"}))
    assert await run_noc_telemetria() == 0
    db.expire_all()
    assert estado.carregar_cursores(db)["amostrasEquipamento"] is None
    assert estado.retrato_de_equipamentos_entregue(db) == (None, None)

    rota.mock(return_value=httpx.Response(202, json={"recebido": True, "duplicado": False}))
    assert await run_noc_telemetria() == 1
    primeiro = _corpo(rota)
    assert primeiro["versaoDoContrato"] == 1
    amostras = primeiro["amostrasDeVerificacao"]
    assert [set(a) for a in amostras] == [{"id", "verificacao", "em", "ok", "valor"}] * 3
    assert [(a["ok"], a["valor"]) for a in amostras] == [(True, 10), (False, None), (True, 12)]
    assert not amostras[0]["em"].endswith("Z")
    [evento] = primeiro["eventosDeEquipamento"]
    assert set(evento) == {"id", "equipamento", "verificacao", "tipo", "de", "para", "em", "detalhe"}
    assert (evento["tipo"], evento["de"], evento["para"]) == ("equipamento.caiu", "no_ar", "fora")
    retrato = {e["ref"]: e for e in primeiro["equipamentos"]}
    assert set(retrato) == {"noc-nvr", "noc-cam-1"}
    nvr = retrato["noc-nvr"]
    assert set(nvr) == {
        "id", "ref", "tipo", "nome", "host", "mac", "fabricante", "modelo", "paiId",
        "tentativasParaCair", "estado", "estadoDesde", "verificacoes", "credenciais", "cftv",
    }  # fmt: skip
    # Plano 1: sem credencial e sem leitura de CFTV, as duas partes vão vazias.
    assert (nvr["credenciais"], nvr["cftv"]) == ([], None)
    assert set(nvr["verificacoes"][0]) == {
        "id", "ref", "tipo", "parametros", "intervaloS", "estado", "estadoDesde",
        "ultimoValor", "ultimaEm", "ultimoErro",
    }  # fmt: skip
    assert retrato["noc-cam-1"]["paiId"] == nvr["id"]

    # Nada mudou: o próximo lote sai sem retrato, e as partes por cursor vazias.
    assert await run_noc_telemetria() == 1
    segundo = _corpo(rota)
    assert "equipamentos" not in segundo
    assert segundo["amostrasDeVerificacao"] == [] and segundo["eventosDeEquipamento"] == []

    # Latência que oscila não é mudança.
    db.expire_all()
    vf = db.query(VerificacaoDeEquipamento).first()
    assert vf is not None
    vf.ultimo_valor, vf.ultima_em = 99, _agora()
    db.commit()
    assert await run_noc_telemetria() == 1
    assert "equipamentos" not in _corpo(rota)

    # Mudança de estado é: o retrato vai de novo.
    db.expire_all()
    eq = db.get(Equipamento, de[0]["id"])
    assert eq is not None
    eq.estado, eq.estado_desde = "fora", _agora()
    db.commit()
    assert await run_noc_telemetria() == 1
    assert {e["id"]: e["estado"] for e in _corpo(rota)["equipamentos"]}[de[0]["id"]] == "fora"


@respx.mock
async def test_retrato_de_seguranca_aos_15_minutos_e_no_reenrolamento(db, backups) -> None:
    _enrolado_direto(db, AutoridadeDeTeste())
    await _criar_dois(db)
    rota = respx.post(URL).mock(return_value=httpx.Response(202, json={"recebido": True}))
    assert await run_noc_telemetria() == 1
    assert "equipamentos" in _corpo(rota)
    assert await run_noc_telemetria() == 1
    assert "equipamentos" not in _corpo(rota)

    velho = (_agora() - telemetria.RETRATO_DE_SEGURANCA - timedelta(seconds=1)).isoformat()
    estado.gravar(db, {estado.KEY_EQUIPAMENTOS_EM: velho})
    db.commit()
    assert await run_noc_telemetria() == 1
    assert "equipamentos" in _corpo(rota)

    _enrolado_direto(db, AutoridadeDeTeste())
    assert await run_noc_telemetria() == 1
    assert "equipamentos" in _corpo(rota)


@respx.mock
async def test_lista_vazia_viaja_como_lista_vazia(db) -> None:
    _enrolado_direto(db, AutoridadeDeTeste())
    rota = respx.post(URL).mock(return_value=httpx.Response(202, json={"recebido": True}))
    assert await run_noc_telemetria() == 1
    assert _corpo(rota)["equipamentos"] == []


async def test_definir_muda_o_retrato_do_proximo_lote(db, backups) -> None:
    de = await _criar_dois(db)
    retrato = lista.retrato(db)
    sha = lista.sha256_do_retrato(retrato)
    para = _para_de(de)
    para[0]["nome"] = "Outro nome"
    await _processar(_definir(de, para))
    db.expire_all()
    assert lista.sha256_do_retrato(lista.retrato(db)) != sha


def test_limites_do_lote() -> None:
    assert telemetria.LIMITE_AMOSTRAS_DE_VERIFICACAO == 5000
    assert telemetria.LIMITE_EVENTOS_DE_EQUIPAMENTO == 2000


async def test_fila_de_amostras_anda_em_varios_lotes(db, backups, monkeypatch) -> None:
    await _com_leituras(db)
    monkeypatch.setattr(telemetria, "LIMITE_AMOSTRAS_DE_VERIFICACAO", 2)
    cur = telemetria.cursores(db)
    lote, novos, mais = telemetria.montar_lote(db, cur)
    assert len(lote["amostrasDeVerificacao"]) == 2 and mais is True
    lote2, novos2, mais2 = telemetria.montar_lote(db, novos)
    assert len(lote2["amostrasDeVerificacao"]) == 1 and mais2 is False
    assert novos2["amostrasEquipamento"] > novos["amostrasEquipamento"]
