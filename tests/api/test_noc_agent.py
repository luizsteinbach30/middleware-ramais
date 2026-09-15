"""Agente do NOC (v2.13.0) — enrolamento, mTLS, heartbeat, manifesto e as recusas.

O NOC é simulado com ``respx``, **assinando de verdade** o CSR que o agente manda
com uma autoridade de teste: sem isso, o teste não provaria que a chave gerada
aqui e o certificado recebido formam um par que o ``ssl`` aceita carregar. O
contrato do outro lado é testado no repositório do NOC
(``api/test/fase0.e2e-spec.ts``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import select

from middleware_monitor.core.models import AppConfig
from middleware_monitor.core.scheduler import get_scheduler
from middleware_monitor.domain.auth.service import bootstrap_admin
from middleware_monitor.domain.backup import bundle as bundle_mod
from middleware_monitor.domain.noc import certificado, cliente, estado, manifesto
from middleware_monitor.jobs.noc_agent import JOB_ID, run_noc_heartbeat

NOC = "https://noc.teste"
CANAL = "https://agente.noc.teste"
AGENTE_ID = "ag_0123456789abcdef01234567"
CREDENCIAL = f"{AGENTE_ID}." + "s" * 43


class AutoridadeDeTeste:
    """O papel do NOC: assina o CSR do agente para ``CN=<identificador>``."""

    def __init__(self) -> None:
        self.chave = ec.generate_private_key(ec.SECP256R1())
        nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "autoridade de teste")])
        agora = datetime.now(UTC)
        self.certificado = (
            x509.CertificateBuilder()
            .subject_name(nome)
            .issuer_name(nome)
            .public_key(self.chave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(agora - timedelta(minutes=5))
            .not_valid_after(agora + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(self.chave, hashes.SHA256())
        )

    def assinar(self, csr_pem: str, dias: int = 365, chave_publica=None) -> str:
        csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
        assert csr.is_signature_valid
        agora = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, AGENTE_ID)]))
            .issuer_name(self.certificado.subject)
            .public_key(chave_publica or csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(agora - timedelta(minutes=5))
            .not_valid_after(agora + timedelta(days=dias))
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
            .sign(self.chave, hashes.SHA256())
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


@pytest.fixture(autouse=True)
def _cache_limpo():
    certificado.limpar_cache_para_testes()
    yield
    certificado.limpar_cache_para_testes()


def _authed(client, db) -> str:
    user, plaintext = bootstrap_admin(db)
    user.must_change_password = False
    db.commit()
    r = client.post("/api/auth/login", json={"username": user.username, "password": plaintext})
    assert r.status_code == 200, r.json()
    return client.cookies.get("mm_csrf") or ""


def _mock_noc(
    ca: AutoridadeDeTeste | None = None, *, heartbeat: httpx.Response | None = None
) -> dict[str, respx.Route]:
    ca = ca or AutoridadeDeTeste()

    def enrolar(request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "agenteId": AGENTE_ID,
                "credencial": CREDENCIAL,
                "intervaloHeartbeatS": 60,
                "certificado": ca.assinar(corpo["csr"]),
                "urlDoCanal": CANAL,
            },
        )

    def renovar(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"certificado": ca.assinar(json.loads(request.content)["csr"], dias=400)}
        )

    return {
        "enrolar": respx.post(f"{NOC}/agente/v1/enrolar").mock(side_effect=enrolar),
        "heartbeat": respx.post(f"{CANAL}/agente/v1/heartbeat").mock(
            return_value=heartbeat
            or httpx.Response(
                200,
                json={
                    "intervaloHeartbeatS": 90,
                    "versaoDesejada": "2.13.0",
                    "enviarManifesto": True,
                    "renovarCertificado": False,
                    "horaDoNoc": "2099-01-01T00:00:00.000Z",
                    "urlDoCanal": CANAL,
                },
            )
        ),
        "manifesto": respx.post(f"{CANAL}/agente/v1/manifesto").mock(return_value=httpx.Response(204)),
        "certificado": respx.post(f"{CANAL}/agente/v1/certificado").mock(side_effect=renovar),
    }


def _enrolar(client, csrf: str, codigo: str = "K7P2-9QX4-M3TD"):
    return client.post(
        "/api/noc/enrolar", json={"codigo": codigo, "url": NOC}, headers={"X-CSRF-Token": csrf}
    )


def _enrolado_direto(db, ca: AutoridadeDeTeste | None = None) -> AutoridadeDeTeste:
    """Estado de agente já enrolado, sem passar pela rota: credencial + par instalado."""
    ca = ca or AutoridadeDeTeste()
    par = certificado.gerar_par("maquina-de-teste")
    expira = certificado.instalar(par, ca.assinar(par.csr_pem))
    estado.guardar_credencial(
        db, url=NOC, agente_id=AGENTE_ID, credencial=CREDENCIAL, intervalo_s=60, user_id=None
    )
    estado.gravar(db, {estado.KEY_URL_CANAL: CANAL, estado.KEY_CERTIFICADO_EXPIRA: expira.isoformat()})
    db.commit()
    return ca


def test_sem_enrolamento_nao_existe_job_nem_conexao(client, db) -> None:
    _authed(client, db)
    r = client.get("/api/noc")
    assert r.status_code == 200
    assert r.json()["situacao"] == "nao_enrolado"
    assert r.json()["url"] == estado.URL_PADRAO
    assert get_scheduler().get_job(JOB_ID) is None


@respx.mock
def test_enrolar_gera_a_chave_aqui_e_instala_o_certificado(client, db) -> None:
    csrf = _authed(client, db)
    rotas = _mock_noc()

    r = _enrolar(client, csrf, codigo="k7p2-9qx4-m3td")
    assert r.status_code == 200, r.json()
    corpo = r.json()
    assert corpo["situacao"] == "conectado"
    assert corpo["agente_id"] == AGENTE_ID
    assert corpo["url_canal"] == CANAL
    assert corpo["certificado_expira_em"] is not None
    # O intervalo é do NOC: a resposta do heartbeat manda, não o padrão local.
    assert corpo["intervalo_heartbeat_s"] == 90
    assert corpo["relogio_offset_s"] > 0

    # O NOC recebeu só o CSR — nenhuma chave privada saiu daqui.
    enviado = json.loads(rotas["enrolar"].calls.last.request.content)
    assert set(enviado) == {"codigo", "maquina", "versao", "sistema", "csr"}
    assert "PRIVATE KEY" not in rotas["enrolar"].calls.last.request.content.decode()
    assert enviado["csr"].startswith("-----BEGIN CERTIFICATE REQUEST-----")

    # A chave está no disco, cifrada, e o par abre no ssl.
    chave = (certificado.diretorio() / certificado.NOME_DA_CHAVE).read_bytes()
    assert b"ENCRYPTED PRIVATE KEY" in chave
    assert not (certificado.diretorio() / (certificado.NOME_DA_CHAVE + certificado.SUFIXO_NOVO)).exists()
    certificado.contexto_do_agente()

    # A credencial NÃO está em claro no banco.
    db.expire_all()
    linha = db.scalar(select(AppConfig).where(AppConfig.key == estado.KEY_CREDENCIAL))
    assert linha is not None and linha.is_secret
    assert CREDENCIAL not in linha.value

    # O heartbeat foi ao CANAL, com a credencial e o hash do manifesto enviado.
    hb = rotas["heartbeat"].calls.last.request
    assert str(hb.url).startswith(CANAL)
    assert hb.headers["authorization"] == f"Bearer {CREDENCIAL}"
    manifesto_enviado = json.loads(rotas["manifesto"].calls.last.request.content)
    assert json.loads(hb.content)["manifestoSha256"] == manifesto_enviado["sha256"]
    assert manifesto.sha256_do_manifesto(manifesto_enviado) == manifesto_enviado["sha256"]
    assert get_scheduler().get_job(JOB_ID) is not None


@respx.mock
def test_certificado_que_nao_casa_com_a_chave_nao_e_instalado(client, db) -> None:
    csrf = _authed(client, db)
    ca = AutoridadeDeTeste()
    outra_chave = ec.generate_private_key(ec.SECP256R1()).public_key()

    def enrolar(request: httpx.Request) -> httpx.Response:
        csr = json.loads(request.content)["csr"]
        return httpx.Response(
            201,
            json={
                "agenteId": AGENTE_ID,
                "credencial": CREDENCIAL,
                "certificado": ca.assinar(csr, chave_publica=outra_chave),
                "urlDoCanal": CANAL,
            },
        )

    respx.post(f"{NOC}/agente/v1/enrolar").mock(side_effect=enrolar)
    r = _enrolar(client, csrf)
    assert r.status_code == 502
    assert "não corresponde" in r.json()["detail"]
    assert not certificado.existe()
    assert client.get("/api/noc").json()["enrolado"] is False


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
    assert not certificado.existe()
    assert not (certificado.diretorio() / (certificado.NOME_DA_CHAVE + certificado.SUFIXO_NOVO)).exists()
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
def test_certificado_recusado_continua_tentando(client, db) -> None:
    """Se o NOC restaurar um backup, parar a frota inteira seria uma visita por site."""
    csrf = _authed(client, db)
    _mock_noc(
        heartbeat=httpx.Response(
            401, json={"codigo": "CERTIFICADO_INVALIDO", "mensagem": "Certificado de cliente recusado."}
        )
    )
    corpo = _enrolar(client, csrf).json()
    assert corpo["situacao"] == "credencial_recusada"
    assert get_scheduler().get_job(JOB_ID) is not None


@respx.mock
async def test_manifesto_so_sobe_quando_o_noc_pede(db) -> None:
    _enrolado_direto(db)
    rotas = _mock_noc(
        heartbeat=httpx.Response(
            200, json={"intervaloHeartbeatS": 60, "versaoDesejada": "2.13.0", "enviarManifesto": False}
        )
    )
    depois = await run_noc_heartbeat()
    assert depois.situacao == "conectado"
    assert rotas["heartbeat"].call_count == 1
    assert rotas["manifesto"].call_count == 0


@respx.mock
async def test_renova_o_certificado_quando_o_noc_pede(db) -> None:
    ca = _enrolado_direto(db)
    antes = certificado.expira_em()
    chave_antes = (certificado.diretorio() / certificado.NOME_DA_CHAVE).read_bytes()
    rotas = _mock_noc(
        ca,
        heartbeat=httpx.Response(
            200, json={"intervaloHeartbeatS": 60, "enviarManifesto": False, "renovarCertificado": True}
        ),
    )
    depois = await run_noc_heartbeat()
    assert rotas["certificado"].call_count == 1
    assert "PRIVATE KEY" not in rotas["certificado"].calls.last.request.content.decode()
    assert certificado.expira_em() > antes
    assert (certificado.diretorio() / certificado.NOME_DA_CHAVE).read_bytes() != chave_antes
    assert depois.certificado_expira_em == certificado.expira_em()
    certificado.contexto_do_agente()


@respx.mock
async def test_renovacao_que_falha_mantem_o_par_que_funciona(db) -> None:
    ca = _enrolado_direto(db)
    chave_antes = (certificado.diretorio() / certificado.NOME_DA_CHAVE).read_bytes()
    rotas = _mock_noc(
        ca,
        heartbeat=httpx.Response(200, json={"intervaloHeartbeatS": 60, "renovarCertificado": True}),
    )
    rotas["certificado"].mock(return_value=httpx.Response(503, json={"mensagem": "fora"}))
    depois = await run_noc_heartbeat()
    assert depois.situacao == "conectado"
    assert "não renovado" in (depois.detalhe or "")
    assert (certificado.diretorio() / certificado.NOME_DA_CHAVE).read_bytes() == chave_antes
    assert not (certificado.diretorio() / (certificado.NOME_DA_CHAVE + certificado.SUFIXO_NOVO)).exists()


async def test_credencial_ilegivel_quando_a_chave_da_instalacao_muda(db, monkeypatch) -> None:
    _enrolado_direto(db)
    from middleware_monitor.settings import get_settings

    monkeypatch.setenv("APP_SECRET_KEY", "outra-chave-completamente-diferente")
    get_settings.cache_clear()
    depois = await run_noc_heartbeat()
    assert depois.situacao == "credencial_ilegivel"
    assert "enrole de novo" in (depois.detalhe or "")


async def test_sem_arquivo_de_certificado_pede_para_enrolar_de_novo(db) -> None:
    _enrolado_direto(db)
    certificado.apagar()
    depois = await run_noc_heartbeat()
    assert depois.situacao == "credencial_ilegivel"
    assert "enrole de novo" in (depois.detalhe or "")


@respx.mock
def test_desenrolar_apaga_a_identidade_e_mantem_o_endereco(client, db) -> None:
    csrf = _authed(client, db)
    _mock_noc()
    _enrolar(client, csrf)
    assert certificado.existe()

    # Com credencial guardada, trocar o endereço é recusado.
    r = client.put("/api/noc/url", json={"url": "outro.noc"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409

    r = client.post("/api/noc/desenrolar", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["situacao"] == "nao_enrolado"
    assert r.json()["url"] == NOC
    assert get_scheduler().get_job(JOB_ID) is None
    assert not certificado.existe()
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
    _enrolado_direto(db)
    data = bundle_mod.build(db, ("config",))
    chaves = {c["key"] for c in data["sections"]["config"]["app_config"]}
    assert not any(k.startswith("noc.") for k in chaves)
    assert CREDENCIAL not in json.dumps(data)
    assert "PRIVATE KEY" not in json.dumps(data)


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
