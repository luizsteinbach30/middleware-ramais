"""Agente do NOC (v2.13.0) — enrolamento, heartbeat, manifesto e as recusas.

O NOC é simulado com ``respx``: o que se testa aqui é o lado do middleware — o
que ele guarda, o que ele manda, e o que ele faz com cada resposta. O contrato
do outro lado é testado no repositório do NOC (``api/test/fase0.e2e-spec.ts``).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from sqlalchemy import select

from middleware_monitor.core.models import AppConfig
from middleware_monitor.core.scheduler import get_scheduler
from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.backup import bundle as bundle_mod
from middleware_monitor.domain.noc import cliente, estado, manifesto
from middleware_monitor.jobs.noc_agent import JOB_ID, run_noc_heartbeat

NOC = "https://noc.teste"
AGENTE_ID = "ag_0123456789abcdef01234567"
CREDENCIAL = f"{AGENTE_ID}." + "s" * 43


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def _mock_noc(*, heartbeat: httpx.Response | None = None) -> dict[str, respx.Route]:
    return {
        "enrolar": respx.post(f"{NOC}/agente/v1/enrolar").mock(
            return_value=httpx.Response(
                201,
                json={"agenteId": AGENTE_ID, "credencial": CREDENCIAL, "intervaloHeartbeatS": 60},
            )
        ),
        "heartbeat": respx.post(f"{NOC}/agente/v1/heartbeat").mock(
            return_value=heartbeat
            or httpx.Response(
                200,
                json={
                    "intervaloHeartbeatS": 90,
                    "versaoDesejada": "2.13.0",
                    "enviarManifesto": True,
                    "horaDoNoc": "2099-01-01T00:00:00.000Z",
                },
            )
        ),
        "manifesto": respx.post(f"{NOC}/agente/v1/manifesto").mock(return_value=httpx.Response(204)),
    }


def _enrolar(client, csrf: str, codigo: str = "K7P2-9QX4-M3TD"):
    return client.post(
        "/api/noc/enrolar", json={"codigo": codigo, "url": NOC}, headers={"X-CSRF-Token": csrf}
    )


def test_sem_enrolamento_nao_existe_job_nem_conexao(client, db) -> None:
    _authed(client, db)
    r = client.get("/api/noc")
    assert r.status_code == 200
    assert r.json()["situacao"] == "nao_enrolado"
    assert r.json()["url"] == estado.URL_PADRAO
    assert get_scheduler().get_job(JOB_ID) is None


@respx.mock
def test_enrolar_guarda_credencial_cifrada_e_faz_o_primeiro_heartbeat(client, db) -> None:
    csrf = _authed(client, db)
    rotas = _mock_noc()

    r = _enrolar(client, csrf, codigo="k7p2-9qx4-m3td")
    assert r.status_code == 200, r.json()
    corpo = r.json()
    assert corpo["situacao"] == "conectado"
    assert corpo["agente_id"] == AGENTE_ID
    # O intervalo é do NOC: a resposta do heartbeat manda, não o padrão local.
    assert corpo["intervalo_heartbeat_s"] == 90
    assert corpo["versao_desejada"] == "2.13.0"
    assert corpo["relogio_offset_s"] > 0

    # O código foi como digitado; a máquina e a versão foram junto.
    enviado = json.loads(rotas["enrolar"].calls.last.request.content)
    assert enviado["codigo"] == "k7p2-9qx4-m3td"
    assert set(enviado) == {"codigo", "maquina", "versao", "sistema"}

    # A credencial NÃO está em claro no banco.
    db.expire_all()
    linha = db.scalar(select(AppConfig).where(AppConfig.key == estado.KEY_CREDENCIAL))
    assert linha is not None and linha.is_secret
    assert CREDENCIAL not in linha.value
    assert estado.ler_credencial(db) == CREDENCIAL

    # O heartbeat foi com a credencial e com o hash do manifesto que foi enviado.
    hb = rotas["heartbeat"].calls.last.request
    assert hb.headers["authorization"] == f"Bearer {CREDENCIAL}"
    manifesto_enviado = json.loads(rotas["manifesto"].calls.last.request.content)
    assert json.loads(hb.content)["manifestoSha256"] == manifesto_enviado["sha256"]
    assert manifesto.sha256_do_manifesto(manifesto_enviado) == manifesto_enviado["sha256"]

    # E o laço ficou agendado.
    assert get_scheduler().get_job(JOB_ID) is not None


@respx.mock
def test_codigo_recusado_mostra_a_mensagem_do_noc_e_nao_guarda_nada(client, db) -> None:
    csrf = _authed(client, db)
    respx.post(f"{NOC}/agente/v1/enrolar").mock(
        return_value=httpx.Response(
            401, json={"codigo": "CODIGO_INVALIDO", "mensagem": "Código inválido, expirado ou já usado."}
        )
    )
    r = _enrolar(client, csrf)
    assert r.status_code == 400
    assert r.json()["detail"] == "Código inválido, expirado ou já usado."
    assert client.get("/api/noc").json()["enrolado"] is False
    assert get_scheduler().get_job(JOB_ID) is None


@respx.mock
def test_noc_fora_do_ar_e_502_e_resposta_sem_json_nao_e_sucesso(client, db) -> None:
    csrf = _authed(client, db)
    respx.post(f"{NOC}/agente/v1/enrolar").mock(side_effect=httpx.ConnectError("recusou"))
    assert _enrolar(client, csrf).status_code == 502

    # Um proxy que devolve 200 com HTML não pode virar "enrolado".
    respx.post(f"{NOC}/agente/v1/enrolar").mock(return_value=httpx.Response(200, text="<html>portal</html>"))
    r = _enrolar(client, csrf)
    assert r.status_code == 502
    assert "sem JSON" in r.json()["detail"]
    assert client.get("/api/noc").json()["enrolado"] is False


@respx.mock
def test_revogado_para_o_laco(client, db) -> None:
    csrf = _authed(client, db)
    _mock_noc(
        heartbeat=httpx.Response(
            401, json={"codigo": "AGENTE_REVOGADO", "mensagem": "Este agente foi revogado no NOC."}
        )
    )
    corpo = _enrolar(client, csrf).json()
    assert corpo["situacao"] == "revogado"
    assert corpo["detalhe"] == "Este agente foi revogado no NOC."
    assert get_scheduler().get_job(JOB_ID) is None


@respx.mock
def test_credencial_recusada_continua_tentando(client, db) -> None:
    """Se o NOC restaurar um backup, parar a frota inteira seria uma visita por site."""
    csrf = _authed(client, db)
    _mock_noc(
        heartbeat=httpx.Response(
            401, json={"codigo": "CREDENCIAL_INVALIDA", "mensagem": "Credencial inválida."}
        )
    )
    corpo = _enrolar(client, csrf).json()
    assert corpo["situacao"] == "credencial_recusada"
    assert get_scheduler().get_job(JOB_ID) is not None


@respx.mock
async def test_manifesto_so_sobe_quando_o_noc_pede(db) -> None:
    estado.guardar_credencial(
        db, url=NOC, agente_id=AGENTE_ID, credencial=CREDENCIAL, intervalo_s=60, user_id=None
    )
    db.commit()
    rotas = _mock_noc(
        heartbeat=httpx.Response(
            200, json={"intervaloHeartbeatS": 60, "versaoDesejada": "2.13.0", "enviarManifesto": False}
        )
    )
    depois = await run_noc_heartbeat()
    assert depois.situacao == "conectado"
    assert rotas["heartbeat"].call_count == 1
    assert rotas["manifesto"].call_count == 0


async def test_credencial_ilegivel_quando_a_chave_da_instalacao_muda(db, monkeypatch) -> None:
    estado.guardar_credencial(
        db, url=NOC, agente_id=AGENTE_ID, credencial=CREDENCIAL, intervalo_s=60, user_id=None
    )
    db.commit()
    from middleware_monitor.settings import get_settings

    monkeypatch.setenv("APP_SECRET_KEY", "outra-chave-completamente-diferente")
    get_settings.cache_clear()
    depois = await run_noc_heartbeat()
    assert depois.situacao == "credencial_ilegivel"
    assert "enrole de novo" in (depois.detalhe or "")


@respx.mock
def test_desenrolar_apaga_a_identidade_e_mantem_o_endereco(client, db) -> None:
    csrf = _authed(client, db)
    _mock_noc()
    _enrolar(client, csrf)

    # Com credencial guardada, trocar o endereço é recusado.
    r = client.put("/api/noc/url", json={"url": "outro.noc"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409

    r = client.post("/api/noc/desenrolar", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["situacao"] == "nao_enrolado"
    assert r.json()["url"] == NOC
    assert get_scheduler().get_job(JOB_ID) is None
    db.expire_all()
    assert db.scalar(select(AppConfig).where(AppConfig.key == estado.KEY_CREDENCIAL)) is None

    r = client.put("/api/noc/url", json={"url": "outro.noc/"}, headers={"X-CSRF-Token": csrf})
    assert r.json()["url"] == "https://outro.noc"


def test_enrolar_exige_admin_e_csrf(client, db) -> None:
    _authed(client, db)
    assert client.post("/api/noc/enrolar", json={"codigo": "K7P2-9QX4-M3TD"}).status_code == 403


def test_manifesto_nao_declara_acao_remota_nem_carrega_segredo(client, db) -> None:
    """Nesta versão o agente não executa nada a pedido do NOC — declarar seria prometer."""
    _authed(client, db)
    m = client.get("/api/noc/manifesto").json()
    assert m["acoes"] == []
    assert m["executorRemoto"] is False
    assert m["versaoDoContrato"] == 1
    texto = json.dumps(m).lower()
    for proibido in ("senha", "password", "token", "credencial", "secret"):
        assert proibido not in texto


def test_identidade_do_noc_nao_viaja_no_pacote_portavel(db) -> None:
    estado.guardar_credencial(
        db, url=NOC, agente_id=AGENTE_ID, credencial=CREDENCIAL, intervalo_s=60, user_id=None
    )
    db.commit()
    data = bundle_mod.build(db, ("config",))
    chaves = {c["key"] for c in data["sections"]["config"]["app_config"]}
    assert not any(k.startswith("noc.") for k in chaves)
    assert CREDENCIAL not in json.dumps(data)


@pytest.mark.parametrize(
    ("entrada", "saida"),
    [
        ("noc.workconnect.com.br", "https://noc.workconnect.com.br"),
        ("https://noc.workconnect.com.br/", "https://noc.workconnect.com.br"),
        ("http://192.168.0.10:3006", "http://192.168.0.10:3006"),
    ],
)
def test_normalizar_url(entrada: str, saida: str) -> None:
    assert cliente.normalizar_url(entrada) == saida


def test_normalizar_url_recusa_esquema_estranho() -> None:
    with pytest.raises(ValueError):
        cliente.normalizar_url("ftp://noc")
    with pytest.raises(ValueError):
        cliente.normalizar_url("  ")
