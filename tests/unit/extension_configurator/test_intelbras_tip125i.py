"""Testes do adapter Intelbras TIP 125i (platwip — SQL via db.cgi).

Cobertura:
  - Fingerprint pelo realm do 401 (sem credencial) e recusa de outro embarcado
  - discover parseia /status.cgi -> modelo "TIP 125i", MAC e firmware
  - generate_config produz SQL determinístico com a conta SIP + hora + teclas
  - REGRA INVIOLAVEL: nenhuma tabela/coluna de rede vaza (whitelist)
  - Pegadinha do fuso: Brasilia e -181, NAO o offset cru (-180 = Newfoundland)
  - send_config manda o base64 e SO ENTAO chama notify.cgi; erro do db.cgi
    (que vem com HTTP 200) vira excecao
  - Conta SIP do banco e 0-based (sip_account=2 -> Account=1)
"""

from __future__ import annotations

import base64
import urllib.parse

import httpx
import pytest
import respx

from middleware_monitor.integrations.extension_configurator.vendors.base import (
    VendorAuthError,
    VendorCredentials,
)
from middleware_monitor.integrations.extension_configurator.vendors.intelbras_tip125i import (
    IntelbrasTIP125iAdapter,
)

IP = "10.150.51.101"
CREDS = VendorCredentials(username="admin", password="admin")

# Resposta real do aparelho de bancada (10.150.51.101, fw 5.0.2), reduzida.
STATUS_JSON = """{
"account":{"user1":"200","user2":"0"},
"net":{"add_ipv4":"10.150.51.101","mac":"98:E5:5B:1B:57:AF","gateway_ipv4":"10.150.10.1"},
"system":{"hwVersion":"17","swMajor":"5","swMinor":"0","swPatch":"2","host_name":"intelbras"},
"product":"tip125",
"branch":"i"
}"""


def _template(**over: object) -> dict:
    base = {
        "sip_server": "10.150.51.50",
        "sip_transport": "udp",
        "sip_account": 1,
        "register_expiration": 30,
        "ntp_server": "a.ntp.br",
        "timezone": "America/Sao_Paulo",
        "timezone_offset_minutes": -180,
        "lcd_language": "pt-BR",
        "keylock_enable": 2,
        "keylock_password": "123",
        "menu_password": "123",
        "nova_web_user": "",
        "nova_web_password": "",
        "function_keys": [
            {
                "key": "LineKey2",
                "type": "speed_dial",
                "label": "Central",
                "value_source": "linha",
                "value_field": "numero_abreviado",
                "account": 1,
            },
        ],
    }
    base.update(over)
    return base


def _row(**over: object) -> dict:
    base = {
        "conta_sip": "1042",
        "senha_sip": "segredo",
        "servidor_sip": "",
        "label": "Recepcao",
        "display_name": "Recepcao",
        "auth_id": "1042",
        "numero_abreviado": "9000",
        "account_active": 1,
    }
    base.update(over)
    return base


# --------------------------------------------------------------- fingerprint
@pytest.mark.asyncio
async def test_fingerprint_realm_intelbras() -> None:
    """O 401 do lighttpd ja identifica a plataforma, sem credencial."""
    a = IntelbrasTIP125iAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/").mock(
            return_value=httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": f'Basic realm="IP phone Intelbras ({IP})"',
                    "Server": "lighttpd/1.4.45",
                },
            ),
        )
        assert await a.fingerprint(IP) == 1.0


@pytest.mark.asyncio
async def test_fingerprint_outro_embarcado_com_lighttpd_e_fraco() -> None:
    """lighttpd pedindo Basic sem o realm da Intelbras nao decide sozinho."""
    a = IntelbrasTIP125iAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/").mock(
            return_value=httpx.Response(
                401,
                headers={"WWW-Authenticate": 'Basic realm="router"', "Server": "lighttpd/1.4.45"},
            ),
        )
        assert await a.fingerprint(IP) == 0.3


@pytest.mark.asyncio
async def test_fingerprint_outro_vendor_zero() -> None:
    a = IntelbrasTIP125iAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/").mock(
            return_value=httpx.Response(200, headers={"Server": "GoAhead-Webs"}),
        )
        assert await a.fingerprint(IP) == 0.0


# ------------------------------------------------------------------ discover
@pytest.mark.asyncio
async def test_discover_status_cgi() -> None:
    a = IntelbrasTIP125iAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/status.cgi").mock(
            return_value=httpx.Response(200, text=STATUS_JSON),
        )
        res = await a.discover(IP, CREDS)
    assert res.vendor == "intelbras_tip125i"
    assert res.model == "TIP 125i"  # product "tip125" + branch "i"
    assert res.mac == "98:E5:5B:1B:57:AF"
    assert res.firmware == "5.0.2"


@pytest.mark.asyncio
async def test_discover_401_vira_auth_error() -> None:
    a = IntelbrasTIP125iAdapter()
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/status.cgi").mock(return_value=httpx.Response(401))
        with pytest.raises(VendorAuthError):
            await a.discover(IP, CREDS)


# ----------------------------------------------------------- generate_config
def test_generate_config_conta_sip() -> None:
    sql = IntelbrasTIP125iAdapter().generate_config(_template(), _row()).decode()
    assert "PhoneNumber='1042'" in sql
    assert "AuthUserName='1042'" in sql
    assert "AuthPassword='segredo'" in sql
    assert "CallerIDName='Recepcao'" in sql
    assert "ServerAddress='10.150.51.50'" in sql  # linha sem servidor herda o do ambiente
    assert "Transport='0'" in sql  # udp
    assert "WHERE Account = 0;" in sql  # conta 1 do usuario = Account 0 no banco
    assert "UPDATE TAB_TEL_ACCOUNT SET AccountActive=1 WHERE Account = 0;" in sql


def test_generate_config_e_deterministico() -> None:
    """O middleware hasheia a saida para decidir se a linha esta `applied`."""
    a = IntelbrasTIP125iAdapter()
    assert a.generate_config(_template(), _row()) == a.generate_config(_template(), _row())


def test_conta_2_vira_account_1() -> None:
    sql = (
        IntelbrasTIP125iAdapter()
        .generate_config(_template(sip_account=2), _row())
        .decode()
    )
    assert "WHERE Account = 1;" in sql


def test_aspa_simples_na_senha_e_escapada() -> None:
    """A UI do aparelho so escapa a PRIMEIRA aspa (bug); aqui escapamos todas."""
    sql = (
        IntelbrasTIP125iAdapter()
        .generate_config(_template(), _row(senha_sip="a'b'c"))
        .decode()
    )
    assert "AuthPassword='a''b''c'" in sql


def test_fuso_brasilia_nao_usa_o_offset_cru() -> None:
    """-180 e "(GMT-03:00) Newfoundland" no firmware; Brasilia e -181."""
    sql = IntelbrasTIP125iAdapter().generate_config(_template(), _row()).decode()
    assert "SYSTimeTimeZone=-181" in sql
    assert "SYSTimeTimeZone=-180" not in sql


def test_fuso_sem_ambiguidade_usa_o_offset() -> None:
    sql = (
        IntelbrasTIP125iAdapter()
        .generate_config(
            _template(timezone="America/Rio_Branco", timezone_offset_minutes=-300), _row(),
        )
        .decode()
    )
    assert "SYSTimeTimeZone=-300" in sql


def test_ntp_ligado_usa_2() -> None:
    """A web UI grava 2 para NTP ligado (1 = desligado); 0 do schema nao e usado."""
    sql = IntelbrasTIP125iAdapter().generate_config(_template(), _row()).decode()
    assert "SYSTimeEnableNTP=2" in sql


def test_softkey_speed_dial() -> None:
    sql = IntelbrasTIP125iAdapter().generate_config(_template(), _row()).decode()
    # Type 6 = Discagem Rapida; Account 0-based; Value vem da linha.
    assert "UPDATE TAB_SOFTKEY SET Type=6,Value='9000',Account=0,Number='' WHERE PK = 2;" in sql


def test_softkey_label_nao_vai_para_number() -> None:
    """`Number` so e editavel para BLF/Gravacao — nao e rotulo de tecla."""
    sql = IntelbrasTIP125iAdapter().generate_config(_template(), _row()).decode()
    assert "Central" not in sql


def test_softkey_fora_do_alcance_e_ignorada() -> None:
    """PK 11+ e modulo de expansao, que o 125i nao tem."""
    tpl = _template(function_keys=[{"key": "LineKey22", "type": "speed_dial", "value_fixed": "1"}])
    sql = IntelbrasTIP125iAdapter().generate_config(tpl, _row()).decode()
    assert "TAB_SOFTKEY" not in sql


def test_sem_nova_senha_nao_mexe_na_credencial_web() -> None:
    sql = IntelbrasTIP125iAdapter().generate_config(_template(), _row()).decode()
    assert "TAB_SECURITY_ACCOUNT" not in sql


def test_nova_senha_web_troca_a_do_usuario_informado() -> None:
    tpl = _template(nova_web_user="admin", nova_web_password="nova123")
    sql = IntelbrasTIP125iAdapter().generate_config(tpl, _row()).decode()
    assert (
        "UPDATE TAB_SECURITY_ACCOUNT SET SECPassword='nova123' WHERE SECAccount = 'admin';" in sql
    )


# ------------------------------------------------------------------ whitelist
def test_whitelist_sem_tabela_de_rede() -> None:
    """REGRA INVIOLAVEL: config de rede do aparelho nunca e tocada."""
    tpl = _template(nova_web_user="admin", nova_web_password="nova123")
    sql = IntelbrasTIP125iAdapter().generate_config(tpl, _row()).decode()
    proibidas = (
        "TAB_NET_ETH_WAN", "TAB_NET_ETH_LAN", "TAB_NET_VLAN", "TAB_NET_SYSLOG", "TAB_LLDP",
    )
    for tabela in proibidas:
        assert tabela not in sql, f"tabela de rede vazou: {tabela}"


def test_whitelist_aborta_coluna_de_rede() -> None:
    with pytest.raises(RuntimeError, match="whitelist"):
        IntelbrasTIP125iAdapter._assert_whitelist(
            "UPDATE TAB_NET_ETH_WAN SET ETHAddressIP='10.0.0.9' WHERE PK = 1;",
        )


def test_whitelist_aborta_coluna_desconhecida_em_tabela_permitida() -> None:
    with pytest.raises(RuntimeError, match="whitelist"):
        IntelbrasTIP125iAdapter._assert_whitelist(
            "UPDATE TAB_VOIP_ACCOUNT SET StunServerIP='1.2.3.4' WHERE Account = 0;",
        )


def test_whitelist_aborta_verbo_que_nao_e_update() -> None:
    """DELETE/DROP/INSERT nao tem o que fazer aqui — o adapter so atualiza."""
    with pytest.raises(RuntimeError, match="statement nao reconhecido"):
        IntelbrasTIP125iAdapter._assert_whitelist("DELETE FROM TAB_VOIP_ACCOUNT;")


# --------------------------------------------------------------- send_config
@pytest.mark.asyncio
async def test_send_config_manda_o_sql_em_base64_percent_encodado() -> None:
    """O Base64 vai como NOME de parametro, entao o httpx encoda `+`/`/`.

    Nao e detalhe estetico: medido em bancada, o Base64 CRU com `+` ou `/` faz o
    aparelho responder 401 — a query crua da web UI do proprio telefone quebra
    dependendo do conteudo. O `=` final que sobra e ignorado pelo firmware.
    """
    a = IntelbrasTIP125iAdapter()
    cfg = a.generate_config(_template(), _row())
    visto: list[httpx.Request] = []

    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/db.cgi").mock(
            side_effect=lambda r: (visto.append(r), httpx.Response(200, text='[{"affected":1}]'))[1],
        )
        router.get(f"https://{IP}/notify.cgi").mock(return_value=httpx.Response(200))
        router.post(f"https://{IP}/restart_control_call.cgi").mock(
            return_value=httpx.Response(200),
        )
        await a.send_config(IP, CREDS, cfg)

    query = visto[0].url.query.decode()
    assert "+" not in query and "/" not in query, f"base64 cru quebra no aparelho: {query}"
    # `params={payload: ""}` -> a query e `<base64 encodado>=`; o valor e vazio.
    payload = urllib.parse.unquote(query.rsplit("=", 1)[0])
    assert b"PhoneNumber='1042'" in base64.b64decode(payload)


@pytest.mark.asyncio
async def test_erro_do_db_cgi_vira_excecao_apesar_do_http_200() -> None:
    """O db.cgi responde 200 com {"error": ...} — quem checa e o adapter."""
    a = IntelbrasTIP125iAdapter()
    cfg = a.generate_config(_template(), _row())
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/db.cgi").mock(
            return_value=httpx.Response(
                200, text='[{"error":"LuaSQL: no such column: Foo [UPDATE ...]"}]',
            ),
        )
        notify = router.get(f"https://{IP}/notify.cgi").mock(return_value=httpx.Response(200))
        router.post(f"https://{IP}/restart_control_call.cgi").mock(
            return_value=httpx.Response(200),
        )
        with pytest.raises(RuntimeError, match="LuaSQL"):
            await a.send_config(IP, CREDS, cfg)
    assert not notify.called, "nao pode notificar depois de erro de SQL"


@pytest.mark.asyncio
async def test_send_config_401_vira_auth_error() -> None:
    a = IntelbrasTIP125iAdapter()
    cfg = a.generate_config(_template(), _row())
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/db.cgi").mock(return_value=httpx.Response(401))
        with pytest.raises(VendorAuthError):
            await a.send_config(IP, CREDS, cfg)


@pytest.mark.asyncio
async def test_send_config_recusa_sql_fora_da_whitelist() -> None:
    """Defesa em profundidade: o envio revalida o payload que recebeu."""
    a = IntelbrasTIP125iAdapter()
    with respx.mock(assert_all_called=False) as router:
        db = router.get(f"https://{IP}/db.cgi").mock(return_value=httpx.Response(200))
        with pytest.raises(RuntimeError, match="whitelist"):
            await a.send_config(
                IP, CREDS, b"UPDATE TAB_NET_ETH_WAN SET ETHAddressIP='10.0.0.9' WHERE PK = 1;\n",
            )
    assert not db.called


# --------------------------------------------------------------- roteamento
def test_modelo_tip_roteia_para_este_adapter_e_nao_para_o_v_series() -> None:
    from middleware_monitor.domain.extension_configurator.service import adapter_for

    assert adapter_for("Intelbras TIP 125i").vendor_id == "intelbras_tip125i"
    assert adapter_for("intelbras tip125i").vendor_id == "intelbras_tip125i"
    # As outras duas plataformas Intelbras seguem intactas.
    assert adapter_for("Intelbras V5501").vendor_id == "intelbras"
    assert adapter_for("Intelbras S3002").vendor_id == "intelbras_s3002"


# ------------------------------------- a pegadinha do `;` final (bug do firmware)
def test_sql_gerado_termina_exatamente_no_ponto_e_virgula() -> None:
    """Sobra depois do `;` final faz o db.cgi descartar TUDO em silencio.

    Medido em bancada: `...;\n` e `...; ` respondem HTTP 200 com corpo vazio e
    nao executam nada. Sem esta garantia, o pipeline marcaria a linha como
    aplicada sem o telefone ter recebido a config.
    """
    cfg = IntelbrasTIP125iAdapter().generate_config(_template(), _row())
    assert cfg.endswith(b";"), f"payload termina com sobra: {cfg[-10:]!r}"


@pytest.mark.asyncio
async def test_corpo_vazio_nao_passa_por_sucesso() -> None:
    """HTTP 200 sem corpo = comando descartado; nao pode virar 'aplicado'."""
    a = IntelbrasTIP125iAdapter()
    cfg = a.generate_config(_template(), _row())
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/db.cgi").mock(return_value=httpx.Response(200, text=""))
        notify = router.get(f"https://{IP}/notify.cgi").mock(return_value=httpx.Response(200))
        router.post(f"https://{IP}/restart_control_call.cgi").mock(
            return_value=httpx.Response(200),
        )
        with pytest.raises(RuntimeError, match="descartado"):
            await a.send_config(IP, CREDS, cfg)
    assert not notify.called


@pytest.mark.asyncio
async def test_envio_remove_sobra_do_payload_recebido() -> None:
    """Defesa em profundidade: mesmo recebendo SQL com sobra, o envio limpa."""
    a = IntelbrasTIP125iAdapter()
    visto: list[httpx.Request] = []
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/db.cgi").mock(
            side_effect=lambda r: (visto.append(r), httpx.Response(200, text='[{"affected":1}]'))[1],
        )
        router.get(f"https://{IP}/notify.cgi").mock(return_value=httpx.Response(200))
        router.post(f"https://{IP}/restart_control_call.cgi").mock(
            return_value=httpx.Response(200),
        )
        await a.send_config(
            IP, CREDS, b"UPDATE TAB_TEL_ACCOUNT SET AccountActive=1 WHERE Account = 0;\n \n",
        )
    payload = urllib.parse.unquote(visto[0].url.query.decode().rsplit("=", 1)[0])
    assert base64.b64decode(payload).endswith(b";")


# ------------------------- o telefone fica preso na sessão SIP anterior (bancada)
@pytest.mark.asyncio
async def test_send_config_reinicia_o_controle_de_chamadas_no_fim() -> None:
    """Sem o restart o aparelho segue registrado com a credencial ANTIGA.

    Medido em bancada: notify sozinho grava e "aplica", mas a sessão SIP não é
    refeita. E o caminho intuitivo não resolve — desativar a conta derruba o
    registro em 1 s e religar não o traz de volta. Quem religa é o
    `restart_control_call.cgi`, o mesmo que a web UI do telefone usa.
    """
    a = IntelbrasTIP125iAdapter()
    cfg = a.generate_config(_template(), _row())
    chamadas: list[str] = []

    def _reg(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        if request.url.path == "/db.cgi":
            return httpx.Response(200, text='[{"affected":1}]')
        return httpx.Response(200, text="")

    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/db.cgi").mock(side_effect=_reg)
        router.get(f"https://{IP}/notify.cgi").mock(side_effect=_reg)
        router.post(f"https://{IP}/restart_control_call.cgi").mock(side_effect=_reg)
        await a.send_config(IP, CREDS, cfg)

    assert chamadas == ["/db.cgi", "/notify.cgi", "/restart_control_call.cgi"]


@pytest.mark.asyncio
async def test_restart_sem_resposta_nao_falha_a_aplicacao() -> None:
    """O CGI reinicia a pilha que serve a própria request: timeout é o normal.

    Tratar isso como erro faria toda aplicação bem-sucedida ser reportada como
    falha (foi o que aconteceu na bancada antes da correção).
    """
    a = IntelbrasTIP125iAdapter()
    cfg = a.generate_config(_template(), _row())
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/db.cgi").mock(
            return_value=httpx.Response(200, text='[{"affected":1}]'),
        )
        router.get(f"https://{IP}/notify.cgi").mock(return_value=httpx.Response(200))
        router.post(f"https://{IP}/restart_control_call.cgi").mock(
            side_effect=httpx.ReadTimeout("sem resposta"),
        )
        await a.send_config(IP, CREDS, cfg)  # não pode levantar


@pytest.mark.asyncio
async def test_erro_de_sql_nao_reinicia_o_aparelho() -> None:
    a = IntelbrasTIP125iAdapter()
    cfg = a.generate_config(_template(), _row())
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/db.cgi").mock(
            return_value=httpx.Response(200, text='[{"error":"LuaSQL: x"}]'),
        )
        restart = router.post(f"https://{IP}/restart_control_call.cgi").mock(
            return_value=httpx.Response(200),
        )
        with pytest.raises(RuntimeError, match="LuaSQL"):
            await a.send_config(IP, CREDS, cfg)
    assert not restart.called


# --------------------------------- valores que o firmware não consegue receber
def test_ponto_e_virgula_no_valor_e_recusado_com_o_nome_do_campo() -> None:
    """O CGI corta o comando no `;` e o aparelho responde 401.

    Sem esta recusa, o adapter leria 401 como "credencial recusada" — o pipeline
    tentaria a outra senha e reportaria um problema de autenticação inexistente.
    """
    from middleware_monitor.integrations.extension_configurator.vendors.intelbras_tip125i import (
        TIP125iValorInvalido,
    )

    with pytest.raises(TIP125iValorInvalido, match="senha SIP"):
        IntelbrasTIP125iAdapter().generate_config(_template(), _row(senha_sip="ab;cd"))
    with pytest.raises(TIP125iValorInvalido, match="nome visível"):
        IntelbrasTIP125iAdapter().generate_config(
            _template(), _row(display_name="Recepcao; Loja"),
        )


def test_quebra_de_linha_no_valor_e_recusada() -> None:
    from middleware_monitor.integrations.extension_configurator.vendors.intelbras_tip125i import (
        TIP125iValorInvalido,
    )

    with pytest.raises(TIP125iValorInvalido, match="controle"):
        IntelbrasTIP125iAdapter().generate_config(_template(), _row(display_name="Sala\nReuniao"))


def test_valor_normal_com_hash_e_aspa_continua_passando() -> None:
    """A recusa é cirúrgica: `#` e `'` atravessam o db.cgi intactos (bancada)."""
    sql = (
        IntelbrasTIP125iAdapter()
        .generate_config(_template(), _row(senha_sip="s3nh@'test#"))
        .decode()
    )
    assert "AuthPassword='s3nh@''test#'" in sql


# ------------------------------------------- UPDATE que não encontrou a linha
@pytest.mark.asyncio
async def test_affected_zero_nao_passa_por_sucesso() -> None:
    """0 linhas = o registro não existe; o UPDATE não fez nada.

    `affected` conta linhas que casaram com o WHERE (gravar o mesmo valor ainda
    devolve 1), então 0 é sempre erro real — conta SIP fora do alcance, ou
    `nova_web_user` que não é um usuário do aparelho.
    """
    a = IntelbrasTIP125iAdapter()
    cfg = a.generate_config(_template(), _row())
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{IP}/db.cgi").mock(
            return_value=httpx.Response(200, text='[{"affected":1},{"affected":0}]'),
        )
        restart = router.post(f"https://{IP}/restart_control_call.cgi").mock(
            return_value=httpx.Response(200),
        )
        with pytest.raises(RuntimeError, match="não encontrou o registro"):
            await a.send_config(IP, CREDS, cfg)
    assert not restart.called
