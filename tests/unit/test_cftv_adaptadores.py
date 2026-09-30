"""Adaptadores de CFTV (``integrations/cftv/``) — AGENTE-NOC item 23, CONTRATO §13.9.

Dois jeitos de provar, para cada fabricante:

- **contra o simulador** (``tools/simuladores/``), em 127.0.0.1 numa porta livre, com a
  autenticação real (digest escrito à mão no simulador; login + cookie + CSRF no UniFi)
  e o cenário mudado ao vivo: canal sem vídeo, disco com defeito/cheio, hora errada,
  senha recusada, resposta vazia e malformada;
- **contra respostas montadas da documentação** (``test_doc_…``, com ``respx``), para
  não depender só do simulador que nós mesmos escrevemos.

E, sempre: **só leitura** — o simulador registra cada pedido, e só passa ``GET`` (mais o
``POST`` de login do UniFi).
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
import respx

from middleware_monitor.integrations.cftv import registry, unifi
from middleware_monitor.integrations.cftv.base import Alvo, ErroDeLeitura, RespostaInvalida, SenhaRecusada
from tools.simuladores import hikvision as sim_hikvision
from tools.simuladores import intelbras as sim_intelbras
from tools.simuladores import unifi as sim_unifi

SENHA = "simulador123"
FABRICAS = {
    "intelbras": (sim_intelbras.SimuladorIntelbras, sim_intelbras.montar_servidor),
    "hikvision": (sim_hikvision.SimuladorHikvision, sim_hikvision.montar_servidor),
    "unifi": (sim_unifi.SimuladorUnifi, sim_unifi.montar_servidor),
}


@pytest.fixture(autouse=True)
def _sessoes_limpas() -> Iterator[None]:
    unifi.esquecer_sessoes()
    yield
    unifi.esquecer_sessoes()


class _NoAr:
    def __init__(self, adaptador: str) -> None:
        classe, montar = FABRICAS[adaptador]
        self.adaptador = adaptador
        self.sim = classe()
        self.servidor = montar(self.sim, 0)
        self.porta = self.servidor.server_address[1]
        self._thread = threading.Thread(target=self.servidor.serve_forever, daemon=True)
        self._thread.start()

    def alvo(self, senha: str = SENHA) -> Alvo:
        return Alvo(host="127.0.0.1", esquema="http", porta=self.porta, usuario="admin", senha=senha)

    async def ler(self, senha: str = SENHA):
        return await registry.get_adapter(self.adaptador).ler(self.alvo(senha), timeout_s=5)

    def fechar(self) -> None:
        self.servidor.shutdown()
        self.servidor.server_close()


@pytest.fixture(params=["intelbras", "hikvision", "unifi"])
def gravador(request) -> Iterator[_NoAr]:
    g = _NoAr(request.param)
    yield g
    g.fechar()


def _agora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- Contra o simulador ---------------------------------------------------------------------------


async def test_le_canais_discos_e_hora_so_com_get(gravador) -> None:
    leitura = await gravador.ler()
    assert [c.numero for c in leitura.canais] == [1, 2, 3, 4]
    assert all(c.video == "ok" and c.gravando is True for c in leitura.canais)
    assert all(c.nome for c in leitura.canais)
    assert len(leitura.discos) == 1
    disco = leitura.discos[0]
    assert (disco.numero, disco.estado) == (1, "ok")
    assert disco.total_gb == pytest.approx(2000, abs=1)
    assert disco.livre_gb == pytest.approx(800, abs=1)
    assert leitura.hora_do_gravador is not None
    assert abs((leitura.hora_do_gravador - _agora()).total_seconds()) < 5
    assert leitura.modelo and leitura.firmware
    metodos = {m for m, _ in gravador.sim.pedidos}
    if gravador.adaptador == "unifi":
        assert gravador.sim.pedidos[0] == ("POST", "/api/auth/login")
        assert {m for m, _ in gravador.sim.pedidos[1:]} == {"GET"}
    else:
        assert metodos == {"GET"}


async def test_canal_sem_video_e_canal_que_nao_grava(gravador) -> None:
    gravador.sim.mudar({"semVideo": [2], "naoGravando": [3]})
    leitura = await gravador.ler()
    por_numero = {c.numero: c for c in leitura.canais}
    assert por_numero[2].video == "sem_video"
    assert por_numero[3].video == "ok" and por_numero[3].gravando is False
    assert por_numero[1].video == "ok" and por_numero[1].gravando is True


async def test_disco_com_defeito(gravador) -> None:
    gravador.sim.mudar(
        {
            "discos": [
                {"estado": "ok", "totalGb": 1000, "livreGb": 10},
                {"estado": "defeito", "totalGb": 1000, "livreGb": 0},
            ]
        }
    )
    leitura = await gravador.ler()
    assert [d.estado for d in leitura.discos] == ["ok", "defeito"]


async def test_disco_cheio(gravador) -> None:
    gravador.sim.mudar({"discos": [{"estado": "cheio", "totalGb": 1000, "livreGb": 0}]})
    leitura = await gravador.ler()
    # O UniFi grava por cima do mais antigo: não existe "cheio" lá.
    esperado = "ok" if gravador.adaptador == "unifi" else "cheio"
    assert leitura.discos[0].estado == esperado


async def test_disco_ausente_e_gravador_sem_disco(gravador) -> None:
    if gravador.adaptador == "unifi":
        pytest.skip("o console UniFi não lista baia vazia")
    gravador.sim.mudar({"discos": [{"estado": "ausente", "totalGb": 0, "livreGb": 0}]})
    assert [d.estado for d in (await gravador.ler()).discos] == ["ausente"]
    gravador.sim.mudar({"discos": []})
    assert (await gravador.ler()).discos == []


async def test_hora_errada(gravador) -> None:
    gravador.sim.mudar({"desvioS": 600})
    leitura = await gravador.ler()
    assert leitura.hora_do_gravador is not None
    assert (leitura.hora_do_gravador - _agora()).total_seconds() == pytest.approx(600, abs=5)


async def test_senha_recusada(gravador) -> None:
    with pytest.raises(SenhaRecusada, match="senha recusada"):
        await gravador.ler(senha="errada")


@pytest.mark.parametrize("resposta", ["vazia", "malformada"])
async def test_resposta_vazia_ou_malformada_e_falha(gravador, resposta) -> None:
    gravador.sim.mudar({"resposta": resposta})
    with pytest.raises(RespostaInvalida):
        await gravador.ler()


async def test_gravador_desligado_e_erro_de_leitura(gravador) -> None:
    alvo = gravador.alvo()
    gravador.fechar()
    # Linux recusa na hora ("não conectou"); o Windows insiste no SYN e estoura o tempo.
    with pytest.raises(ErroDeLeitura, match=r"não conectou|não respondeu a tempo"):
        await registry.get_adapter(gravador.adaptador).ler(alvo, timeout_s=2)


async def test_senha_nunca_aparece_no_alvo_nem_no_erro(gravador) -> None:
    alvo = gravador.alvo(senha="S3gredoQueNaoVaza")
    assert "S3gredoQueNaoVaza" not in repr(alvo)
    with pytest.raises(SenhaRecusada) as erro:
        await registry.get_adapter(gravador.adaptador).ler(alvo, timeout_s=5)
    assert "S3gredoQueNaoVaza" not in str(erro.value)


async def test_hikvision_analogico_le_pelo_resdesc() -> None:
    g = _NoAr("hikvision")
    try:
        g.sim.mudar({"analogico": True, "semVideo": [4]})
        leitura = await g.ler()
        assert {c.numero: c.video for c in leitura.canais} == {1: "ok", 2: "ok", 3: "ok", 4: "sem_video"}
        assert ("GET", "/ISAPI/System/Video/inputs/channels") in g.sim.pedidos
    finally:
        g.fechar()


async def test_unifi_reaproveita_a_sessao_e_entra_de_novo_quando_ela_vence() -> None:
    g = _NoAr("unifi")
    try:
        await g.ler()
        await g.ler()
        assert g.sim.ler()["logins"] == 1
        g.sim._sessoes.clear()  # o console esqueceu a sessão
        await g.ler()
        assert g.sim.ler()["logins"] == 2
    finally:
        g.fechar()


def test_porta_do_simulador_e_livre() -> None:
    """Sanidade do próprio teste: o simulador sobe em porta escolhida pelo sistema."""
    g = _NoAr("intelbras")
    try:
        with socket.create_connection(("127.0.0.1", g.porta), timeout=2):
            pass
    finally:
        g.fechar()


# --- Contra respostas da documentação ---------------------------------------------------------------

BASE = "http://10.0.0.50:80"


def _doc_alvo() -> Alvo:
    return Alvo(host="10.0.0.50", esquema="http", porta=80, usuario="admin", senha="x")


def _doc_intelbras(rotas: respx.MockRouter, *, sem_video: httpx.Response) -> None:
    def texto(corpo: str) -> httpx.Response:
        return httpx.Response(200, text=corpo)

    rotas.get(f"{BASE}/cgi-bin/magicBox.cgi?action=getSystemInfo").mock(
        return_value=texto(
            "deviceType=DHI-NVR4104HS-4KS2\r\nprocessor=ST7108\r\nserialNumber=4D1234PAZ00001\r\nupdateSerial=NVR4104HS\r\n"
        )
    )
    rotas.get(f"{BASE}/cgi-bin/magicBox.cgi?action=getSoftwareVersion").mock(
        return_value=texto("version=4.001.0000000.0,build:2021-07-02\r\n")
    )
    rotas.get(f"{BASE}/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle").mock(
        return_value=texto("table.ChannelTitle[0].Name=Entrada\r\ntable.ChannelTitle[1].Name=Caixa 1\r\n")
    )
    rotas.get(f"{BASE}/cgi-bin/eventManager.cgi?action=getEventIndexes&code=VideoLoss").mock(
        return_value=sem_video
    )
    rotas.get(f"{BASE}/cgi-bin/configManager.cgi?action=getConfig&name=RecordMode").mock(
        return_value=texto("table.RecordMode[0].Mode=0\r\ntable.RecordMode[1].Mode=2\r\n")
    )
    rotas.get(f"{BASE}/cgi-bin/eventManager.cgi?action=getEventIndexes&code=StorageLowSpace").mock(
        return_value=texto("")
    )
    rotas.get(f"{BASE}/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo").mock(
        return_value=texto(
            "list.info[0].Detail[0].IsError=false\r\n"
            "list.info[0].Detail[0].Path=/dev/sda1\r\n"
            "list.info[0].Detail[0].TotalBytes=1000203091968.000000\r\n"
            "list.info[0].Detail[0].Type=ReadWrite\r\n"
            "list.info[0].Detail[0].UsedBytes=1000203091968.000000\r\n"
            "list.info[0].Name=/dev/sda\r\n"
            "list.info[0].State=Success\r\n"
        )
    )
    rotas.get(f"{BASE}/cgi-bin/global.cgi?action=getCurrentTime").mock(
        return_value=texto("result=2011-7-3 21:02:32\r\n")
    )


@respx.mock
async def test_doc_intelbras() -> None:
    _doc_intelbras(respx.mock, sem_video=httpx.Response(200, text="channels[0]=1\r\n"))
    leitura = await registry.get_adapter("intelbras").ler(_doc_alvo())
    assert [(c.numero, c.nome, c.video, c.gravando) for c in leitura.canais] == [
        (1, "Entrada", "ok", True),
        (2, "Caixa 1", "sem_video", False),
    ]
    assert leitura.discos[0].estado == "ok" and leitura.discos[0].livre_gb == 0.0
    assert leitura.discos[0].total_gb == 1000.2
    assert leitura.hora_do_gravador is not None
    assert (leitura.hora_do_gravador.year, leitura.hora_do_gravador.month) == (2011, 7)
    assert leitura.modelo == "DHI-NVR4104HS-4KS2"


@respx.mock
async def test_doc_intelbras_event_indexes_com_error_deixa_o_video_desconhecido() -> None:
    _doc_intelbras(respx.mock, sem_video=httpx.Response(200, text="Error\r\nBad Request!\r\n"))
    leitura = await registry.get_adapter("intelbras").ler(_doc_alvo())
    assert {c.video for c in leitura.canais} == {"desconhecido"}


@respx.mock
async def test_doc_intelbras_sem_evento_ativo_responde_vazio_e_e_video_ok() -> None:
    _doc_intelbras(respx.mock, sem_video=httpx.Response(200, text=""))
    leitura = await registry.get_adapter("intelbras").ler(_doc_alvo())
    assert {c.video for c in leitura.canais} == {"ok"}


@respx.mock
async def test_doc_intelbras_200_sem_device_type_nao_e_gravador() -> None:
    respx.get(f"{BASE}/cgi-bin/magicBox.cgi?action=getSystemInfo").mock(
        return_value=httpx.Response(200, text="<html>Web Service</html>")
    )
    with pytest.raises(RespostaInvalida, match="não é um gravador"):
        await registry.get_adapter("intelbras").ler(_doc_alvo())


_NS10 = 'xmlns="http://www.hikvision.com/ver10/XMLSchema" version="1.0"'


@respx.mock
async def test_doc_hikvision() -> None:
    def xml(corpo: str) -> httpx.Response:
        return httpx.Response(200, text=f'<?xml version="1.0" encoding="UTF-8"?>\n{corpo}')

    respx.get(f"{BASE}/ISAPI/System/deviceInfo").mock(
        return_value=xml(
            f"<DeviceInfo {_NS10}><deviceName>Embedded Net DVR</deviceName><model>DS-7608NI-K2</model>"
            "<serialNumber>DS-7608NI-K20820190101CCRRD12345678WCVU</serialNumber>"
            "<firmwareVersion>V4.22.005</firmwareVersion><deviceType>NVR</deviceType></DeviceInfo>"
        )
    )
    respx.get(f"{BASE}/ISAPI/ContentMgmt/InputProxy/channels/status").mock(
        return_value=xml(
            f"<InputProxyChannelStatusList {_NS10}>"
            "<InputProxyChannelStatus><id>1</id><sourceInputPortDescriptor><ipAddress>192.168.254.2</ipAddress>"
            "</sourceInputPortDescriptor><online>true</online></InputProxyChannelStatus>"
            "<InputProxyChannelStatus><id>2</id><online>false</online></InputProxyChannelStatus>"
            "</InputProxyChannelStatusList>"
        )
    )
    respx.get(f"{BASE}/ISAPI/ContentMgmt/InputProxy/channels").mock(return_value=httpx.Response(403))
    respx.get(f"{BASE}/ISAPI/ContentMgmt/record/tracks").mock(
        return_value=xml(
            f"<TrackList {_NS10}><Track><id>101</id><Channel>1</Channel><Enable>true</Enable></Track>"
            "<Track><id>102</id><Channel>1</Channel><Enable>false</Enable></Track>"
            "<Track><id>201</id><Channel>2</Channel><Enable>false</Enable></Track></TrackList>"
        )
    )
    respx.get(f"{BASE}/ISAPI/ContentMgmt/Storage/hdd").mock(
        return_value=xml(
            f"<hddList {_NS10}><hdd><id>1</id><hddName>hdd1</hddName><hddPath></hddPath>"
            "<hddType>SATA</hddType>"
            "<status>ok</status><capacity>953869</capacity><freeSpace>0</freeSpace><property>RW</property></hdd>"
            "<hdd><id>2</id><status>smartFailed</status><capacity>953869</capacity><freeSpace>1024</freeSpace></hdd>"
            "</hddList>"
        )
    )
    respx.get(f"{BASE}/ISAPI/System/time").mock(
        return_value=xml(
            f"<Time {_NS10}><timeMode>NTP</timeMode><localTime>2019-01-01T08:00:00-03:00</localTime>"
            "<timeZone>CST+3:00:00</timeZone></Time>"
        )
    )
    leitura = await registry.get_adapter("hikvision").ler(_doc_alvo())
    assert [(c.numero, c.video, c.gravando, c.nome) for c in leitura.canais] == [
        (1, "ok", True, None),
        (2, "sem_video", False, None),
    ]
    assert [d.estado for d in leitura.discos] == ["ok", "defeito"]
    assert leitura.discos[0].total_gb == 1000.2
    assert leitura.hora_do_gravador == datetime(2019, 1, 1, 11, 0, 0)
    assert leitura.modelo == "DS-7608NI-K2"


@respx.mock
async def test_doc_hikvision_xml_com_entidade_e_recusado() -> None:
    respx.get(f"{BASE}/ISAPI/System/deviceInfo").mock(
        return_value=httpx.Response(
            200, text='<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><DeviceInfo>&a;</DeviceInfo>'
        )
    )
    with pytest.raises(RespostaInvalida, match="DTD"):
        await registry.get_adapter("hikvision").ler(_doc_alvo())


@respx.mock
async def test_doc_hikvision_raiz_errada_e_falha() -> None:
    respx.get(f"{BASE}/ISAPI/System/deviceInfo").mock(
        return_value=httpx.Response(
            200, text=f"<ResponseStatus {_NS10}><statusCode>4</statusCode></ResponseStatus>"
        )
    )
    with pytest.raises(RespostaInvalida, match="esperado <DeviceInfo>"):
        await registry.get_adapter("hikvision").ler(_doc_alvo())


@respx.mock
async def test_doc_unifi() -> None:
    base = "https://10.0.0.1:443"
    login = respx.post(f"{base}/api/auth/login").mock(
        return_value=httpx.Response(
            200,
            json={"unique_id": "x", "username": "leitura"},
            headers={"Set-Cookie": "TOKEN=abc.def.ghi; path=/; secure; httponly", "X-CSRF-Token": "tok-123"},
        )
    )
    bootstrap = respx.get(f"{base}/proxy/protect/api/bootstrap").mock(
        return_value=httpx.Response(
            200,
            headers={"Date": "Wed, 30 Sep 2026 13:00:00 GMT"},
            json={
                "nvr": {
                    "id": "n1",
                    "type": "UNVR",
                    "version": "2.8.35",
                    "systemInfo": {
                        "storage": {
                            "available": 500_000_000_000,
                            "size": 4_000_000_000_000,
                            "devices": [{"model": "WD40PURZ", "size": 4_000_000_000_000, "healthy": "good"}],
                        }
                    },
                },
                "cameras": [
                    {
                        "id": "b",
                        "name": "Fundos",
                        "state": "DISCONNECTED",
                        "isConnected": False,
                        "isRecording": False,
                    },
                    {
                        "id": "a",
                        "name": "Frente",
                        "state": "CONNECTED",
                        "isConnected": True,
                        "isRecording": True,
                    },
                    {"id": "c", "name": "Não adotada", "isAdopted": False, "isConnected": True},
                ],
            },
        )
    )
    alvo = Alvo(host="10.0.0.1", esquema="https", porta=443, usuario="leitura", senha="x")
    leitura = await registry.get_adapter("unifi").ler(alvo)
    assert login.call_count == 1
    assert bootstrap.calls[0].request.headers["x-csrf-token"] == "tok-123"
    assert "TOKEN=abc.def.ghi" in bootstrap.calls[0].request.headers["cookie"]
    assert [(c.numero, c.nome, c.video, c.gravando) for c in leitura.canais] == [
        (1, "Frente", "ok", True),
        (2, "Fundos", "sem_video", False),
    ]
    assert [(d.estado, d.total_gb, d.livre_gb) for d in leitura.discos] == [("ok", 4000.0, 500.0)]
    assert leitura.hora_do_gravador == datetime(2026, 9, 30, 13, 0, 0)


@respx.mock
async def test_doc_unifi_bootstrap_sem_nvr_e_falha() -> None:
    base = "https://10.0.0.1:443"
    respx.post(f"{base}/api/auth/login").mock(
        return_value=httpx.Response(200, headers={"Set-Cookie": "TOKEN=t; path=/"})
    )
    respx.get(f"{base}/proxy/protect/api/bootstrap").mock(
        return_value=httpx.Response(200, json={"cameras": []})
    )
    alvo = Alvo(host="10.0.0.1", esquema="https", porta=443, usuario="leitura", senha="x")
    with pytest.raises(RespostaInvalida, match="nvr"):
        await registry.get_adapter("unifi").ler(alvo)


@respx.mock
async def test_doc_unifi_login_sem_cookie_nao_e_sucesso() -> None:
    base = "https://10.0.0.1:443"
    respx.post(f"{base}/api/auth/login").mock(return_value=httpx.Response(200, json={}))
    alvo = Alvo(host="10.0.0.1", esquema="https", porta=443, usuario="leitura", senha="x")
    with pytest.raises(RespostaInvalida, match="TOKEN"):
        await registry.get_adapter("unifi").ler(alvo)


def test_registro_tem_os_tres_com_modulo_e_credencial() -> None:
    assert {a.adaptador_id: (a.modulo, a.tipo_de_credencial) for a in registry.list_adapters()} == {
        "intelbras": ("cftv.intelbras", "HTTP"),
        "hikvision": ("cftv.hikvision", "HTTP"),
        "unifi": ("cftv.unifi", "API"),
    }


def test_julgar_gravador_sem_disco_e_canal_sem_video_nao_repete_gravacao() -> None:
    from middleware_monitor.domain.equipamentos import cftv
    from middleware_monitor.integrations.cftv.base import Canal, Disco, LeituraCftv

    agora = _agora()
    leitura = LeituraCftv(
        canais=[Canal(1, None, "sem_video", False), Canal(2, "B", "desconhecido", None)],
        discos=[Disco(1, "ausente", None, None)],
        hora_do_gravador=None,
    )
    lido = cftv.julgar(leitura, agora)
    # Canal sem vídeo não é também "não está gravando"; vídeo desconhecido não é problema.
    assert lido.erro == "canal 1 sem vídeo; nenhum disco no gravador"
    assert (
        lido.retrato is not None
        and lido.retrato["desvioS"] is None
        and lido.retrato["horaDoGravador"] is None
    )
