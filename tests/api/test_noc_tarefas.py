"""Tarefas do NOC (v2.13.0, Fases 2 e 3) — o executor, a idempotência e as recusas.

O NOC é simulado com ``respx``; o aparelho, com ``monkeypatch`` nas funções que a
tela já usa (``run_action_on_line``, ``_send_config_with_fallback``). O que se
prova aqui:

- nada fora da lista de permissão roda, e nenhum campo de rede passa (item 10);
- tarefa repetida devolve o resultado gravado **sem executar de novo** (item 3);
- escrita interrompida nunca roda de novo; escrita sem prazo ou sem backup não começa;
- leitura não muda o estado que o vigia de recuperação usa;
- o resultado sai com a chave da tarefa e fica no outbox até o NOC confirmar.

O lado do NOC está em ``noc-workconnect/api/test/fase2.e2e-spec.ts``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import ClassVar

import httpx
import pytest
import respx
from sqlalchemy import select

from middleware_monitor.core.models import Device, ExtensionApplyRunLine, NocTarefa, SystemLog
from middleware_monitor.domain.backup import snapshot
from middleware_monitor.domain.extension_configurator import repository as repo
from middleware_monitor.domain.noc import certificado, executor, manifesto
from middleware_monitor.domain.uscall import repository as uscall_repo
from middleware_monitor.integrations.extension_configurator.vendors import (
    DEVICE_ACTIONS,
    ActionResult,
    FlyingVoiceAdapter,
    HTEKAdapter,
    IntelbrasAdapter,
    IntelbrasS3002Adapter,
    IntelbrasTIP125iAdapter,
    YealinkAdapter,
)
from middleware_monitor.jobs import noc_agent
from tests.api.test_noc_agent import CANAL, CREDENCIAL, _enrolado_direto

RESULTADO = "/agente/v1/tarefas/{id}/resultado"


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    certificado.limpar_cache_para_testes()
    monkeypatch.setattr(noc_agent, "_laco", noc_agent._Laco())
    yield
    certificado.limpar_cache_para_testes()


def _tarefa(id_: str = "t1", tipo: str = "ping", raio: str = "LEITURA", **extra) -> dict:
    return {
        "id": id_,
        "tipo": tipo,
        "raio": raio,
        "pedido": extra.pop("pedido", {"ramal": "1001"}),
        "pedidaPor": "ana@workconnect.com.br",
        "idempotencia": extra.pop("idempotencia", f"chave-{id_}"),
        "leaseSegundos": extra.pop("leaseSegundos", 900),
        **extra,
    }


async def _processar(tarefa: dict) -> executor.Pronta:
    pronta = await executor.processar(tarefa, canal=CANAL, credencial=CREDENCIAL)
    assert pronta is not None
    return pronta


def _ambiente(db, *linhas: tuple[str, str], modelo: str = "HTEK UC902G"):
    env = repo.create_environment(db, nome="Loja 7", modelo_telefone=modelo)
    repo.update_environment(db, env, config_padrao={"validar_conectividade": False})
    repo.save_lines(db, env, [repo.new_line(ip=ip, numero_ramal=ramal) for ramal, ip in linhas])
    db.commit()
    return env


class _AparelhoFalso:
    """``run_action_on_line`` que conta quantas vezes tocou o telefone."""

    def __init__(self, ok: bool = True) -> None:
        self.chamadas: list[tuple[str, str, str | None]] = []
        self.ok = ok

    async def __call__(self, line_id, action, *, params=None, operador=None):
        self.chamadas.append((line_id, action, operador))
        return ActionResult(ok=self.ok, detail="volume 15, nao perturbe desligado", rebooted=False)


@pytest.fixture
def aparelho(monkeypatch) -> _AparelhoFalso:
    falso = _AparelhoFalso()
    monkeypatch.setattr("middleware_monitor.domain.extension_configurator.actions.run_action_on_line", falso)
    return falso


# --- Item 10: a lista de permissão --------------------------------------------------------


def test_lista_de_permissao_e_fechada_e_nao_tem_rede() -> None:
    """Acrescentar uma ação remota tem de quebrar este teste: é decisão, não detalhe."""
    assert set(executor.ACOES) == {
        "ping",
        "status_do_ramal",
        "coletar_agora",
        "inventario",
        "capacidades",
        "logs",
        "normalize",
        "reaplicar_config_do_ambiente",
    }
    assert "set_ip" not in executor.ACOES
    assert "send_config" not in executor.ACOES
    # Das ações de aparelho, só normalize vai pelo canal remoto — em todo adapter.
    assert set(DEVICE_ACTIONS) & set(executor.ACOES) == {"normalize"}
    for adapter in (
        HTEKAdapter(),
        IntelbrasAdapter(),
        IntelbrasS3002Adapter(),
        IntelbrasTIP125iAdapter(),
        FlyingVoiceAdapter(),
        YealinkAdapter(),
    ):
        assert set(adapter.capabilities()) & set(executor.ACOES) <= {"normalize"}, adapter.vendor_id


@pytest.mark.parametrize(
    ("tipo", "raio", "pedido", "trecho"),
    [
        ("set_ip", "ESCRITA_REVERSIVEL", {"ramal": "1001", "ip": "10.0.0.9"}, "não executa"),
        ("normalize", "ESCRITA_REVERSIVEL", {"ramal": "1001", "ip": "10.0.0.9"}, "P_CODE_DE_REDE"),
        (
            "reaplicar_config_do_ambiente",
            "ESCRITA_REVERSIVEL",
            {"ramal": "1001", "gateway": "x"},
            "P_CODE_DE_REDE",
        ),
        (
            "reaplicar_config_do_ambiente",
            "ESCRITA_REVERSIVEL",
            {"ramal": "1001", "P1234": "1"},
            "P_CODE_DE_REDE",
        ),
        ("normalize", "ESCRITA_REVERSIVEL", {"ramal": "1001", "vlan_id": 3}, "P_CODE_DE_REDE"),
        ("ping", "LEITURA", {"ip": "10.0.0.1"}, "P_CODE_DE_REDE"),
        ("reaplicar_config_do_ambiente", "ESCRITA_REVERSIVEL", {"ramal": "1001", "config": {}}, "não aceita"),
        ("normalize", "LEITURA", {"ramal": "1001"}, "aqui é ESCRITA_REVERSIVEL"),
        ("normalize", "ESCRITA_REVERSIVEL", {}, "precisa de"),
        ("ping", "LEITURA", {"ramal": "10 01; rm"}, "ramal inválido"),
        ("logs", "LEITURA", {"linhas": 10_000}, "entre 1 e 500"),
        ("ping", "LEITURA", ["1001"], "objeto"),
    ],
)
def test_recusas_antes_de_tocar_qualquer_coisa(tipo, raio, pedido, trecho) -> None:
    recusa = executor.conferir(tipo, raio, pedido)
    assert recusa is not None
    assert recusa.nao_suportado is True
    assert trecho in (recusa.erro or "")


async def test_recusa_chega_ao_noc_como_nao_suportado_e_nao_toca_o_aparelho(db, aparelho) -> None:
    _ambiente(db, ("1001", "10.0.0.11"))
    pronta = await _processar(
        _tarefa(tipo="normalize", raio="ESCRITA_REVERSIVEL", pedido={"ramal": "1001", "dns": "8.8.8.8"})
    )
    assert pronta.corpo["naoSuportado"] is True
    assert pronta.corpo["ok"] is False
    assert aparelho.chamadas == []


def test_manifesto_declara_exatamente_o_executor(db) -> None:
    corpo = manifesto.montar(db)
    assert corpo["executorRemoto"] is True
    assert corpo["acoes"] == sorted(executor.ACOES)
    assert "set_ip" not in corpo["acoes"]
    assert corpo["ultimoBackupEm"] is None
    snapshot.create_snapshot(label="manual")
    com_backup = manifesto.montar(db)
    assert com_backup["ultimoBackupEm"].endswith("Z")
    # Backup novo muda o hash: o NOC pede o manifesto e a aprovação mostra a hora certa.
    assert com_backup["sha256"] != corpo["sha256"]


# --- Item 3: idempotência -----------------------------------------------------------------------


async def test_tarefa_repetida_devolve_o_gravado_sem_executar_de_novo(db, aparelho) -> None:
    _ambiente(db, ("1001", "10.0.0.11"))
    tarefa = _tarefa(tipo="normalize", raio="ESCRITA_REVERSIVEL")
    primeira = await _processar(tarefa)
    segunda = await _processar(tarefa)
    assert len(aparelho.chamadas) == 1
    assert primeira.corpo == segunda.corpo
    assert primeira.corpo["ok"] is True
    r = primeira.corpo["resultado"]
    assert r["aplicado"] is True and r["ramal"] == "1001"
    assert r["backup"].startswith("backup-") and r["backup"].endswith(".db.gz")
    # A trilha local diz quem mandou.
    assert aparelho.chamadas[0][2] == "noc:ana@workconnect.com.br"


async def test_escrita_reoferecida_com_outra_chave_tambem_nao_roda(db, aparelho) -> None:
    _ambiente(db, ("1001", "10.0.0.11"))
    await _processar(_tarefa(tipo="normalize", raio="ESCRITA_REVERSIVEL", idempotencia="k1"))
    await _processar(_tarefa(tipo="normalize", raio="ESCRITA_REVERSIVEL", idempotencia="k2"))
    assert len(aparelho.chamadas) == 1


async def test_leitura_reoferecida_com_outra_chave_roda_de_novo(db, monkeypatch) -> None:
    vezes = []

    async def logs(p, ctx):
        vezes.append(ctx.tarefa_id)
        return executor.Resultado(ok=True, resultado={"linhas": []})

    monkeypatch.setitem(
        executor.ACOES, "logs", executor.Acao("LEITURA", logs, opcionais=frozenset({"linhas", "nivel"}))
    )
    await _processar(_tarefa(tipo="logs", pedido={}, idempotencia="k1"))
    await _processar(_tarefa(tipo="logs", pedido={}, idempotencia="k1"))
    await _processar(_tarefa(tipo="logs", pedido={}, idempotencia="k2"))
    assert len(vezes) == 2


async def test_escrita_interrompida_nunca_roda_de_novo(db, aparelho) -> None:
    _ambiente(db, ("1001", "10.0.0.11"))
    agora = _agora()
    db.add_all(
        [
            NocTarefa(
                id="t-escrita",
                tipo="normalize",
                raio="ESCRITA_REVERSIVEL",
                idempotencia="chave-t-escrita",
                pedido='{"ramal": "1001"}',
                recebida_em=agora,
                iniciada_em=agora,
            ),
            NocTarefa(
                id="t-leitura",
                tipo="ping",
                raio="LEITURA",
                idempotencia="k",
                pedido="{}",
                recebida_em=agora,
                iniciada_em=agora,
            ),
        ]
    )
    db.commit()

    assert executor.recuperar_interrompidas() == 2
    db.expire_all()
    assert db.get(NocTarefa, "t-leitura") is None  # leitura: o NOC reoferece
    escrita = db.get(NocTarefa, "t-escrita")
    assert escrita.ok is False and "interrompida" in escrita.erro
    assert [p.tarefa_id for p in executor.a_entregar()] == ["t-escrita"]

    pronta = await _processar(_tarefa("t-escrita", tipo="normalize", raio="ESCRITA_REVERSIVEL"))
    assert pronta.corpo["ok"] is False
    assert aparelho.chamadas == []


async def test_escrita_comecada_que_chega_de_novo_antes_da_recuperacao_nao_roda(db, aparelho) -> None:
    """Mesma garantia sem depender do boot: a marca ``iniciada_em`` basta."""
    _ambiente(db, ("1001", "10.0.0.11"))
    agora = _agora()
    db.add(
        NocTarefa(
            id="t1",
            tipo="normalize",
            raio="ESCRITA_REVERSIVEL",
            idempotencia="chave-t1",
            pedido='{"ramal": "1001"}',
            recebida_em=agora,
            iniciada_em=agora,
        )
    )
    db.commit()
    pronta = await _processar(_tarefa("t1", tipo="normalize", raio="ESCRITA_REVERSIVEL"))
    assert pronta.corpo["ok"] is False and "interrompida" in pronta.corpo["erro"]
    assert aparelho.chamadas == []


# --- Escrita: prazo, backup, alvo ----------------------------------------------------------------


async def test_escrita_sem_prazo_nao_comeca(db, aparelho) -> None:
    _ambiente(db, ("1001", "10.0.0.11"))
    pronta = await _processar(_tarefa(tipo="normalize", raio="ESCRITA_REVERSIVEL", leaseSegundos=30))
    assert pronta.corpo["ok"] is False
    assert "não foi iniciada" in pronta.corpo["erro"]
    assert aparelho.chamadas == []


async def test_escrita_sem_backup_nao_toca_o_aparelho(db, aparelho, monkeypatch) -> None:
    _ambiente(db, ("1001", "10.0.0.11"))

    def quebra(**_):
        raise snapshot.SnapshotError("disco cheio")

    monkeypatch.setattr(snapshot, "create_snapshot", quebra)
    pronta = await _processar(_tarefa(tipo="normalize", raio="ESCRITA_REVERSIVEL"))
    assert pronta.corpo["ok"] is False
    assert "Backup obrigatório" in pronta.corpo["erro"] and "disco cheio" in pronta.corpo["erro"]
    assert aparelho.chamadas == []


async def test_escrita_mira_uma_linha_so(db, aparelho) -> None:
    _ambiente(db, ("1001", "10.0.0.11"))
    env2 = repo.create_environment(db, nome="Loja 8", modelo_telefone="HTEK UC902G")
    repo.save_lines(db, env2, [repo.new_line(ip="10.0.0.99", numero_ramal="1001")])
    db.commit()

    ambigua = await _processar(_tarefa("a", tipo="normalize", raio="ESCRITA_REVERSIVEL"))
    assert ambigua.corpo["ok"] is False and "2 linhas" in ambigua.corpo["erro"]
    ausente = await _processar(
        _tarefa("b", tipo="normalize", raio="ESCRITA_REVERSIVEL", pedido={"ramal": "9999"})
    )
    assert ausente.corpo["ok"] is False and "não está cadastrado" in ausente.corpo["erro"]
    assert aparelho.chamadas == []


async def test_normalize_em_modelo_sem_homologacao_e_nao_suportado(db, aparelho) -> None:
    _ambiente(db, ("1001", "10.0.0.11"), modelo="Intelbras S3002")
    pronta = await _processar(_tarefa(tipo="normalize", raio="ESCRITA_REVERSIVEL"))
    assert pronta.corpo["naoSuportado"] is True
    assert aparelho.chamadas == []


async def test_reaplicar_so_a_linha_do_ramal_e_devolve_linha_a_linha(db, monkeypatch) -> None:
    enviados: list[str] = []

    async def envio_falso(adapter, ip, chain, cfg_bytes):
        enviados.append(ip)

    monkeypatch.setattr(
        "middleware_monitor.domain.extension_configurator.apply._send_config_with_fallback", envio_falso
    )
    _ambiente(db, ("1001", "10.0.0.11"), ("1002", "10.0.0.12"))

    pronta = await _processar(_tarefa(tipo="reaplicar_config_do_ambiente", raio="ESCRITA_REVERSIVEL"))

    assert enviados == ["10.0.0.11"]  # a outra linha do ambiente não foi tocada
    assert pronta.corpo["ok"] is True, pronta.corpo
    r = pronta.corpo["resultado"]
    assert r["ramal"] == "1001" and r["backup"].endswith(".db.gz")
    assert [(ln["ramal"], ln["depois"]) for ln in r["linhas"]] == [("1001", "ok")]
    run_line = db.scalar(select(ExtensionApplyRunLine))
    assert run_line.run.operador == "noc:ana@workconnect.com.br"


# --- Leituras ---------------------------------------------------------------------------------------


class _PingFalso:
    alvos: ClassVar[list[str]] = []

    async def ping(self, ip: str, timeout_ms: int) -> int | None:
        _PingFalso.alvos.append(ip)
        return 7


async def test_ping_usa_o_ip_daqui_e_nao_muda_o_estado_do_dispositivo(db, monkeypatch) -> None:
    monkeypatch.setattr("middleware_monitor.integrations.network.factory.make_ping_probe", _PingFalso)
    _PingFalso.alvos = []
    agora = _agora()
    db.add(
        Device(
            name="1001",
            ip="10.0.0.11",
            network_status="offline",
            network_status_prev="online",
            created_at=agora,
            updated_at=agora,
        )
    )
    db.commit()

    pronta = await _processar(_tarefa())
    r = pronta.corpo["resultado"]
    assert _PingFalso.alvos == ["10.0.0.11"]
    assert r["respondeu"] is True and r["latenciaMs"] == 7 and r["origemDoIp"] == "dispositivo"
    db.expire_all()
    d = db.scalar(select(Device).where(Device.name == "1001"))
    # Se virasse online aqui, o monitor não veria a volta e não reaplicaria a config.
    assert d.network_status == "offline"


@respx.mock
async def test_status_do_ramal_pergunta_ao_uscall_e_nao_devolve_token(db) -> None:
    uscall_repo.create_server(db, nome="PBX", host="pbx.cliente", token_plain="TOKEN-QUE-NAO-SAI")
    db.commit()
    respx.get("https://pbx.cliente/api/extenstatus").mock(
        return_value=httpx.Response(
            200, json=[{"ramal": "1001", "status": "Disponivel", "token": "TOKEN-QUE-NAO-SAI"}]
        )
    )
    pronta = await _processar(_tarefa(tipo="status_do_ramal"))
    assert pronta.corpo["ok"] is True
    assert pronta.corpo["resultado"]["registrado"] is True
    assert pronta.corpo["resultado"]["servidor"] == "PBX"
    assert "TOKEN-QUE-NAO-SAI" not in json.dumps(pronta.corpo)


async def test_logs_saem_sem_segredo(db) -> None:
    db.add(
        SystemLog(
            timestamp=_agora(),
            level="warning",
            module="x",
            message="falhou",
            context=json.dumps({"token": "abc", "senha_sip": "123", "ramal": "1001"}),
        )
    )
    db.commit()
    pronta = await _processar(_tarefa(tipo="logs", pedido={"linhas": 10, "nivel": "WARNING"}))
    linha = pronta.corpo["resultado"]["linhas"][0]
    assert linha["contexto"] == {"token": "***", "senha_sip": "***", "ramal": "1001"}


# --- O laço ---------------------------------------------------------------------------------------------


@respx.mock
async def test_ciclo_executa_e_entrega_com_a_chave_da_tarefa(db, monkeypatch) -> None:
    _enrolado_direto(db)
    monkeypatch.setattr("middleware_monitor.integrations.network.factory.make_ping_probe", _PingFalso)
    agora = _agora()
    db.add(Device(name="1001", ip="10.0.0.11", created_at=agora, updated_at=agora))
    db.commit()

    fila = respx.get(f"{CANAL}/agente/v1/tarefas").mock(
        return_value=httpx.Response(200, json={"tarefas": [_tarefa()]})
    )
    resultado = respx.post(f"{CANAL}{RESULTADO.format(id='t1')}").mock(return_value=httpx.Response(204))

    assert await noc_agent.ciclo_de_tarefas() == 0.0
    assert fila.calls.last.request.url.params["espera"] == "25"
    pedido = resultado.calls.last.request
    assert pedido.headers["Idempotency-Key"] == "chave-t1"
    assert pedido.headers["Authorization"] == f"Bearer {CREDENCIAL}"
    assert json.loads(pedido.content)["resultado"]["respondeu"] is True
    assert executor.a_entregar() == []


@respx.mock
async def test_noc_fora_guarda_o_resultado_e_faz_backoff(db, monkeypatch) -> None:
    _enrolado_direto(db)
    monkeypatch.setattr("middleware_monitor.integrations.network.factory.make_ping_probe", _PingFalso)
    agora = _agora()
    db.add(Device(name="1001", ip="10.0.0.11", created_at=agora, updated_at=agora))
    db.commit()

    respx.get(f"{CANAL}/agente/v1/tarefas").mock(
        return_value=httpx.Response(200, json={"tarefas": [_tarefa()]})
    )
    rota = respx.post(f"{CANAL}{RESULTADO.format(id='t1')}").mock(return_value=httpx.Response(503))
    await noc_agent.ciclo_de_tarefas()
    assert [p.tarefa_id for p in executor.a_entregar()] == ["t1"]  # outbox

    # Próximo ciclo: o outbox não sai, o long-poll nem é tentado, e o atraso cresce.
    atraso = await noc_agent.ciclo_de_tarefas()
    assert atraso is not None and atraso >= 1.0
    assert noc_agent._laco.falhas == 1

    # O NOC volta: o outbox esvazia antes de pedir tarefa nova.
    rota.mock(return_value=httpx.Response(204))
    respx.get(f"{CANAL}/agente/v1/tarefas").mock(return_value=httpx.Response(200, json={"tarefas": []}))
    assert await noc_agent.ciclo_de_tarefas() == 0.0
    assert executor.a_entregar() == []
    assert noc_agent._laco.falhas == 0


@respx.mock
async def test_resultado_que_o_noc_recusa_por_chave_nao_trava_o_outbox(db) -> None:
    _enrolado_direto(db)
    agora = _agora()
    db.add(
        NocTarefa(
            id="velha",
            tipo="logs",
            raio="LEITURA",
            idempotencia="k-velha",
            pedido="{}",
            recebida_em=agora,
            iniciada_em=agora,
            concluida_em=agora,
            ok=True,
        )
    )
    db.commit()
    respx.post(f"{CANAL}{RESULTADO.format(id='velha')}").mock(
        return_value=httpx.Response(422, json={"codigo": "ENTRADA_INVALIDA", "mensagem": "chave diferente"})
    )
    respx.get(f"{CANAL}/agente/v1/tarefas").mock(return_value=httpx.Response(200, json={"tarefas": []}))
    assert await noc_agent.ciclo_de_tarefas() == 0.0
    assert executor.a_entregar() == []


@respx.mock
async def test_revogado_para_o_laco_de_tarefas(db) -> None:
    _enrolado_direto(db)
    respx.get(f"{CANAL}/agente/v1/tarefas").mock(
        return_value=httpx.Response(401, json={"codigo": "AGENTE_REVOGADO", "mensagem": "revogado"})
    )
    assert await noc_agent.ciclo_de_tarefas() is None


async def test_sem_enrolamento_o_laco_nao_existe(db) -> None:
    assert await noc_agent.ciclo_de_tarefas() is None


def test_backoff_tem_jitter_e_teto() -> None:
    valores = {round(noc_agent._atraso_com_jitter(8), 3) for _ in range(20)}
    assert len(valores) > 1  # sem jitter, a frota inteira volta no mesmo milissegundo
    assert all(1.0 <= v <= noc_agent.BACKOFF_MAXIMO_S for v in valores)
    assert all(v <= noc_agent.BACKOFF_MAXIMO_S for v in (noc_agent._atraso_com_jitter(50) for _ in range(20)))
