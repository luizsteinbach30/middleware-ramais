"""Adapter Intelbras TIP 125i (plataforma "platwip" — lighttpd + SPA AngularJS).

ATENCAO: e a TERCEIRA plataforma Intelbras do produto, sem nada em comum com as
outras duas. NAO confundir:
  * `intelbras.py`        V-series  — firmware RapidLogic, upload de XML
  * `intelbras_s3002.py`  linha S   — firmware GoAhead, replay de form .asp
  * `intelbras_tip125i.py` linha TIP — platwip, **SQL direto sobre HTTP**

Validado em lab 2026-08-28 contra 10.150.51.101 (TIP 125i, fw 5.0.2, hw 17):
  Server: lighttpd/1.4.45 ; HTTP :80 -> 301 para https://<ip>:443
  Cert self-signed -> httpx com verify=False.

Auth — HTTP Basic (o mais simples de todos os adapters):
  Nao ha login, sessao, cookie nem token CSRF. Toda request leva
  `Authorization: Basic ...`. Credencial recusada = 401 -> VendorAuthError.

Fingerprint (sem auth, de graca):
  GET https://{ip}/ -> 401 com
      WWW-Authenticate: Basic realm="IP phone Intelbras (<ip>)"
  O realm identifica fabricante E plataforma numa request so, sem credencial.

Discover (autenticado):
  GET /status.cgi -> JSON com `product` ("tip125"), `branch` ("i"),
  `net.mac` e `system.swMajor/swMinor/swPatch` (-> "5.0.2").

Protocolo de configuracao — o firmware expoe o proprio banco:
  GET /db.cgi?<base64(SQL)>       executa o SQL (LuaSQL sobre SQLite).
                                  Varios statements separados por `;` numa
                                  request so, e todos rodam.
      resposta SELECT : [ { "rows": [ {...}, ... ] } ]
      resposta UPDATE : [ { "affected": N } ]
      resposta erro   : [ { "error": "LuaSQL: ..." } ]   (HTTP 200 mesmo assim!)
      resposta VAZIA  : HTTP 200 com corpo vazio = o comando foi DESCARTADO
                        sem executar. Acontece com qualquer sobra depois do `;`
                        do ultimo statement (nova linha, espaco, comentario
                        `--`). E a falha mais perigosa desta plataforma, porque
                        e silenciosa: sem checagem, o pipeline marcaria a linha
                        como aplicada. Ver `_execute` e `_raise_for_db_error`.
  GET /notify.cgi?tables=T1,T2    avisa o firmware que as tabelas mudaram —
                                  e o "aplicar". Sem isso o valor fica gravado
                                  no banco e o aparelho segue com o antigo.
  GET /backup.cgi                 backup nativo (cifrado, OpenSSL "Salted__").

  E o mesmo caminho que a propria web UI usa (`platwipServices.factory("db")`
  em resources/angular/app.js): ela monta o SQL no navegador, codifica em
  Base64 e chama db.cgi. Nao ha API "de verdade" por tras — o SQL E a API.

Whitelist rigida (REGRA INVIOLAVEL — feedback-nunca-tocar-em-rede):
  Aqui a regra e mais facil de garantir que nos outros adapters: como o UPDATE
  so toca as colunas que escrevemos, nao existe replay e nao ha risco de
  arrastar campo alheio. Ainda assim `_assert_whitelist` reparsa o SQL gerado e
  aborta se aparecer QUALQUER par tabela.coluna fora de `_WHITELIST` — as
  tabelas de rede (TAB_NET_ETH_WAN, TAB_NET_ETH_LAN, TAB_NET_VLAN,
  TAB_NET_SYSLOG, TAB_LLDP) nunca entram. Ver test_intelbras_tip125i.py.

Conta SIP: `Account` e 0-based no banco (conta 1 = Account 0). O aparelho traz
  QUATRO contas (Account 0..3, confirmado em bancada e no `status.cgi`, que
  reporta `user1`..`user4`); o `sip_account` do ambiente so endereca as duas
  primeiras, que e o que o produto oferece hoje.

Pegadinha do fuso (SYSTimeTimeZone):
  o campo parece "offset em minutos", mas quando dois fusos dividem o mesmo
  offset o firmware desempata com um sufixo: -180 e "(GMT-03:00) Newfoundland",
  -181 e "(GMT-03:00) Brasilia" e -182 e "Buenos Aires". Mandar o offset cru
  (-180) poria o telefone em Newfoundland. Por isso `_TIMEZONE_IDS` mapeia
  explicitamente, e o fallback (offset cru) so vale para os offsets sem
  ambiguidade. Lista lida de `timeZones` no app.js do proprio aparelho.

Device actions: nenhuma homologada ainda. O `normalize` (volume no maximo + DND
  desligado) nao entra sem prova: o DND e claro (TAB_SERVICE_CODE.DND), mas o
  volume vive em TAB_SOFT_CURRENTCONFIG e essa plataforma NAO tem tela web de
  volume — o valor maximo nao foi confirmado em hardware. Fica para a proxima
  bancada em vez de virar chute.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any

import httpx

from middleware_monitor.core.logging import get_logger

from .base import (
    DiscoveryResult,
    VendorAdapter,
    VendorAuthError,
    VendorCredentials,
)

log = get_logger("extension_configurator.intelbras_tip125i")

# Pares `TABELA.Coluna` que ESTE adapter pode escrever. Tudo o que o SQL gerado
# tocar fora desta lista aborta a geracao. NENHUMA tabela de rede aqui —
# garantido por teste (test_whitelist_sem_tabela_de_rede).
_WHITELIST: frozenset[str] = frozenset({
    # Conta SIP (TAB_VOIP_ACCOUNT) — o essencial do provisionamento.
    "TAB_VOIP_ACCOUNT.PhoneNumber",    # numero/usuario SIP
    "TAB_VOIP_ACCOUNT.AuthUserName",   # auth id
    "TAB_VOIP_ACCOUNT.AuthPassword",   # senha SIP (plaintext no banco)
    "TAB_VOIP_ACCOUNT.CallerIDName",   # display name
    "TAB_VOIP_ACCOUNT.ServerAddress",  # servidor SIP (do PBX, nao config de rede)
    "TAB_VOIP_ACCOUNT.ServerPort",
    "TAB_VOIP_ACCOUNT.Transport",      # 0=UDP 1=TCP 2=TLS
    "TAB_VOIP_ACCOUNT.SendRegister",
    "TAB_VOIP_ACCOUNT.RegisterTimer",
    # Conta habilitada (TAB_TEL_ACCOUNT).
    "TAB_TEL_ACCOUNT.AccountActive",
    # Hora (TAB_SYSTEM_TIME).
    "TAB_SYSTEM_TIME.SYSTimeManual",
    "TAB_SYSTEM_TIME.SYSTimeEnableNTP",
    "TAB_SYSTEM_TIME.SYSTimeNTPFirstAddress",
    "TAB_SYSTEM_TIME.SYSTimeTimeZone",
    # Telefone: idioma do display e bloqueio de teclado (TAB_SYSTEM_PHONE).
    "TAB_SYSTEM_PHONE.SYSPhoneLanguage",
    "TAB_SYSTEM_PHONE.SYSPhonePin",
    "TAB_SYSTEM_PHONE.SYSPhoneLockPhone",
    # Teclas programaveis (TAB_SOFTKEY).
    "TAB_SOFTKEY.Type",
    "TAB_SOFTKEY.Value",
    "TAB_SOFTKEY.Account",
    "TAB_SOFTKEY.Number",
    # Senha do admin web (TAB_SECURITY_ACCOUNT).
    "TAB_SECURITY_ACCOUNT.SECPassword",
})

# Tabelas que o `notify.cgi` precisa conhecer depois do UPDATE, na ordem em que
# a propria web UI as envia ao salvar uma conta.
_NOTIFY_TABLES: tuple[str, ...] = (
    "TAB_VOIP_ACCOUNT",
    "TAB_TEL_ACCOUNT",
    "TAB_SYSTEM_TIME",
    "TAB_SYSTEM_PHONE",
    "TAB_SOFTKEY",
    "TAB_SECURITY_ACCOUNT",
)

# `Transport` do TAB_VOIP_ACCOUNT (VARCHAR). Indices do array
# `transportProtocol` do app.js: ["UDP","TCP","TLS"].
_TRANSPORT_IDS: dict[str, str] = {"udp": "0", "tcp": "1", "tls": "2"}

# IANA -> valor de SYSTimeTimeZone. So entram os pares lidos na tabela
# `timeZones` do app.js do aparelho. Fora daqui cai no offset cru (ver
# _timezone_id), que e o proprio formato do campo quando nao ha ambiguidade.
_TIMEZONE_IDS: dict[str, int] = {
    "America/Sao_Paulo": -181,   # "(GMT-03:00) Brasilia" — NAO -180 (Newfoundland)
    "America/Bahia": -181,
    "America/Fortaleza": -181,
    "America/Recife": -181,
    "America/Belem": -181,
    "America/Araguaina": -181,
    "America/Maceio": -181,
    "America/Santarem": -181,
    "America/Noronha": -120,     # "(GMT-02:00) Mid-Atlantic"
    "America/Manaus": -240,      # "(GMT-04:00) Atlantic Time (Canada)"
    "America/Cuiaba": -240,
    "America/Porto_Velho": -240,
    "America/Boa_Vista": -240,
    "America/Campo_Grande": -240,
    "America/Rio_Branco": -300,  # "(GMT-05:00) Bogota, Lima, Quito"
    "America/Eirunepe": -300,
}

# Tipos de tecla programavel (TAB_SOFTKEY.Type). Indices do array
# `softkeyType` do SoftkeysCtrl (app.js):
#   0 Nao Definido · 1 BLF · 2 Captura Ramal · 3 Captura Grupo · 4 Captura Geral
#   5 Estacionamento · 6 Discagem Rapida · 7 Intercom · 8 Gravacao
#   9 Call Return · 10 Conta · 11 Desvio
_SOFTKEY_TYPES: dict[str, int] = {
    "disabled": 0, "na": 0, "none": 0,
    "blf": 1,
    "pickup": 2, "captura_ramal": 2,
    "group_pickup": 3, "captura_grupo": 3,
    "speed_dial": 6, "discagem_rapida": 6,
    "intercom": 7,
    "line": 10, "conta": 10,
}

# `Account` do TAB_SOFTKEY: 65535 = Auto, 0 = Conta 1, 1 = Conta 2 (app.js,
# `softkeyAccount`). Teclas do aparelho sao PK 1..10 (PK 11..25 e 26..40 sao
# modulos de expansao, que o 125i nao tem).
_SOFTKEY_ACCOUNT_AUTO = 65535
_SOFTKEY_PK_MAX = 10

# SYSTimeEnableNTP: a web UI grava 2 para NTP ligado e 1 para desligado
# (`$scope.time.SYSTimeEnableNTP = 1 == parseInt($scope.enableNTP) ? 2 : 1`).
# O 0 do DEFAULT do schema nao e usado pela UI.
_NTP_ON = 2

# Idioma do display (TAB_SYSTEM_PHONE.SYSPhoneLanguage): o firmware usa
# `pt_BR`/`en_US` (underscore), nao a tag BCP-47 do middleware.
_LCD_LANGUAGES: dict[str, str] = {
    "pt-br": "pt_BR", "pt": "pt_BR", "pt_br": "pt_BR",
    "en-us": "en_US", "en": "en_US", "en_us": "en_US",
    "es-es": "es_ES", "es": "es_ES", "es_es": "es_ES",
}

# `SELECT`/`UPDATE` que o SQL gerado pode conter. Statement fora disso (DELETE,
# DROP, INSERT, PRAGMA, ATTACH...) aborta — o adapter so atualiza o que existe.
_STATEMENT_RE = re.compile(
    r"^UPDATE\s+(?P<tabela>\w+)\s+SET\s+(?P<sets>.+?)(?:\s+WHERE\s+.+)?;$",
    re.IGNORECASE | re.DOTALL,
)
_ASSIGN_RE = re.compile(r"(?P<col>\w+)\s*=")


class TIP125iValorInvalido(ValueError):
    """Valor que o firmware do TIP nao consegue receber (ver `_sql_str`)."""


def _sql_str(value: Any, campo: str = "") -> str:
    """Literal SQL de texto, com a aspa simples escapada.

    A web UI do aparelho faz `.replace(/'/,"''")` — sem a flag `g`, ou seja, so
    escapa a PRIMEIRA aspa (bug do firmware que quebraria um nome como
    `D'Avila's`). Aqui escapamos todas. O `#` nao precisa de tratamento:
    comprovado em bancada que atravessa o db.cgi intacto.

    Ja o `;` e uma quebra que NAO da para escapar: o CGI separa os statements
    por `;` antes de entregar ao SQL, entao um `;` dentro de aspas parte o
    comando ao meio. Medido em bancada: o aparelho responde **401** — a request
    morre antes do CGI e o adapter leria "credencial recusada", mandando o
    pipeline tentar a outra senha e reportar um problema de autenticacao que nao
    existe. Caracteres de controle (nova linha, tab) sao o mesmo tipo de
    problema: gravam sujeira e quebram o re-parse da whitelist.

    Por isso o valor e RECUSADO, e nao silenciosamente limpado: numa senha SIP,
    trocar o caractere sem avisar deixaria o ramal sem registrar e ninguem
    entenderia por que. Falha cedo, com o nome do campo.
    """
    texto = str(value if value is not None else "")
    if ";" in texto:
        raise TIP125iValorInvalido(
            f"TIP 125i: o campo {campo or 'de texto'} contém ';', que o firmware do "
            f"aparelho não aceita (ele corta o comando nesse ponto). Remova o "
            f"ponto-e-vírgula do valor.",
        )
    if any(c < " " or c == "\x7f" for c in texto):
        raise TIP125iValorInvalido(
            f"TIP 125i: o campo {campo or 'de texto'} tem caractere de controle "
            f"(quebra de linha ou tab), que o firmware do aparelho não aceita.",
        )
    return "'" + texto.replace("'", "''") + "'"


def _sql_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class IntelbrasTIP125iAdapter(VendorAdapter):
    vendor_id = "intelbras_tip125i"

    _TIMEOUT = 8.0
    # O restart do controle de chamadas costuma nao responder (ver
    # `_restart_call_control`): timeout curto, para nao segurar o pipeline em
    # massa por 8 s por telefone sem necessidade.
    _RESTART_TIMEOUT = 3.0
    # 401 neste firmware nem sempre e sobre credencial (ver `_executar_com_retry`).
    _AUTH_RETRIES = 2
    _AUTH_RETRY_DELAY_S = 1.5
    _BASE = "https://{ip}"

    _DB = "/db.cgi"
    _NOTIFY = "/notify.cgi"
    _STATUS = "/status.cgi"
    _BACKUP = "/backup.cgi"
    # Reinicio pos-config. O endpoint MUDA com a versao do firmware, e a
    # escolha errada faz o telefone ficar preso na sessao SIP anterior:
    #   fw 5.0.x  -> /restart_control_call.cgi  reinicia SO a pilha de chamadas
    #   fw 4.3.x  -> NAO existe (404); so ha /restart.cgi, que reinicia o
    #                aparelho inteiro (~1 min fora do ar)
    # Medido em bancada nos dois: 404 no 4.3.41, executa no 5.0.2.
    _RESTART_CALL = "/restart_control_call.cgi"
    _RESTART_SYSTEM = "/restart.cgi"

    # ------------------------------------------------------------------ HTTP
    @classmethod
    def _client(cls, ip: str, creds: VendorCredentials | None = None) -> httpx.AsyncClient:
        """Cliente HTTPS do aparelho (cert self-signed) — Basic Auth opcional."""
        return httpx.AsyncClient(
            base_url=cls._BASE.format(ip=ip),
            timeout=cls._TIMEOUT,
            verify=False,  # noqa: S501 — cert self-signed do proprio telefone
            follow_redirects=False,
            auth=(creds.username, creds.password) if creds else None,
        )

    # ----------------------------------------------------------- fingerprint
    async def fingerprint(self, ip: str) -> float:
        """`WWW-Authenticate: Basic realm="IP phone Intelbras (<ip>)"` no 401.

        Identifica fabricante e plataforma sem credencial nenhuma. O V-series
        (RapidLogic) e o S3002 (GoAhead) nao usam Basic Auth, entao nao ha
        colisao entre os tres adapters Intelbras.
        """
        try:
            async with self._client(ip) as client:
                resp = await client.get("/")
        except Exception:
            return 0.0
        if resp.status_code != 401:
            return 0.0
        realm = resp.headers.get("WWW-Authenticate", "")
        if "IP phone Intelbras" in realm:
            return 1.0
        # lighttpd pedindo Basic e sinal fraco: pode ser outro embarcado.
        return 0.3 if "lighttpd" in resp.headers.get("Server", "").lower() else 0.0

    # -------------------------------------------------------------- discover
    async def discover(self, ip: str, creds: VendorCredentials) -> DiscoveryResult:
        """`GET /status.cgi` -> modelo (`product`+`branch`), MAC e firmware."""
        async with self._client(ip, creds) as client:
            resp = await client.get(self._STATUS)
        if resp.status_code in (401, 403):
            raise VendorAuthError(f"TIP 125i {ip}: credencial recusada (HTTP {resp.status_code})")
        resp.raise_for_status()
        data = json.loads(resp.text)

        system = data.get("system") or {}
        net = data.get("net") or {}
        firmware = ".".join(
            str(system.get(k, "")) for k in ("swMajor", "swMinor", "swPatch")
        ).strip(".")
        return DiscoveryResult(
            vendor=self.vendor_id,
            model=self._model_name(data),
            firmware=firmware or None,
            mac=(net.get("mac") or None),
            confidence=1.0,
            raw=data,
        )

    @staticmethod
    def _model_name(status: dict[str, Any]) -> str | None:
        """`{"product":"tip125","branch":"i"}` -> `"TIP 125i"`.

        `branch` e a variante do produto (o "i" do 125i) e vem separado; junta
        sem espaco. Produto sem numero volta como veio, em maiusculas.
        """
        product = str(status.get("product") or "").strip()
        if not product:
            return None
        branch = str(status.get("branch") or "").strip()
        m = re.fullmatch(r"([A-Za-z]+)(\d+)", product)
        nome = f"{m.group(1).upper()} {m.group(2)}" if m else product.upper()
        return f"{nome}{branch}"

    # ------------------------------------------------------- generate_config
    def generate_config(self, template: dict[str, Any], row: dict[str, Any]) -> bytes:
        """Gera o script SQL que provisiona a linha (UPDATEs, um por statement).

        Determinístico (o middleware hasheia a saida para saber se a linha esta
        `applied`): sem timestamp, sem ordem de dict variavel.
        """
        # Conta SIP alvo: `sip_account` do ambiente e 1-based, o banco e 0-based.
        account = 1 if str(template.get("sip_account", 1)) == "2" else 0
        active = 1 if row.get("account_active", 1) in (1, "1", True, "true", "on") else 0

        transport_raw = str(template.get("sip_transport", "udp")).lower()
        transport = _TRANSPORT_IDS.get(
            transport_raw, transport_raw if transport_raw in ("0", "1", "2") else "0",
        )
        conta_sip = str(row.get("conta_sip", ""))
        nome = str(row.get("display_name") or row.get("label") or conta_sip)
        servidor = str(row.get("servidor_sip") or template.get("sip_server", ""))
        # PIN unico do aparelho (nao ha menu_password separado como no V-series).
        pin = template.get("keylock_password") or template.get("menu_password") or "123"

        # SEM comentario SQL: medido em bancada que um `--` em qualquer ponto do
        # payload faz o db.cgi devolver corpo VAZIO com HTTP 200 (nada executa e
        # nada e reportado). Um statement por linha ja da a legibilidade.
        linhas: list[str] = [
            "UPDATE TAB_VOIP_ACCOUNT SET "
            + ",".join([
                f"PhoneNumber={_sql_str(conta_sip, 'ramal')}",
                f"AuthUserName={_sql_str(row.get('auth_id') or conta_sip, 'usuário de autenticação')}",
                f"AuthPassword={_sql_str(row.get('senha_sip', ''), 'senha SIP')}",
                f"CallerIDName={_sql_str(nome, 'nome visível')}",
                f"ServerAddress={_sql_str(servidor, 'servidor SIP')}",
                f"ServerPort={_sql_int(template.get('sip_port'), 5060)}",
                f"Transport={_sql_str(transport, 'transporte')}",
                f"SendRegister={active}",
                f"RegisterTimer={_sql_int(template.get('register_expiration'), 30)}",
            ])
            + f" WHERE Account = {account};",
            f"UPDATE TAB_TEL_ACCOUNT SET AccountActive={active} WHERE Account = {account};",
            "UPDATE TAB_SYSTEM_TIME SET "
            + ",".join([
                "SYSTimeManual=0",
                f"SYSTimeEnableNTP={_NTP_ON}",
                f"SYSTimeNTPFirstAddress={_sql_str(template.get('ntp_server', 'a.ntp.br'), 'servidor NTP')}",
                f"SYSTimeTimeZone={self._timezone_id(template)}",
            ])
            + " WHERE PK = 1;",
            "UPDATE TAB_SYSTEM_PHONE SET "
            + ",".join([
                f"SYSPhoneLanguage={_sql_str(self._lcd_language(template), 'idioma')}",
                f"SYSPhonePin={_sql_str(pin, 'PIN de bloqueio')}",
                f"SYSPhoneLockPhone={1 if _sql_int(template.get('keylock_enable'), 2) else 0}",
            ])
            + " WHERE PK = 1;",
        ]
        linhas.extend(self._render_softkeys(template.get("function_keys", []) or [], row, account))
        linhas.extend(self._render_web_admin(template))

        # SEM nova linha no fim: ver `_execute` — sobra depois do `;` final faz o
        # aparelho engolir o comando inteiro em silencio.
        sql = "\n".join(linhas)
        self._assert_whitelist(sql)
        return sql.encode("utf-8")

    @staticmethod
    def _timezone_id(template: dict[str, Any]) -> int:
        """IANA -> SYSTimeTimeZone, com o desempate do firmware.

        Fallback = offset em minutos, que e o formato do campo nos fusos sem
        ambiguidade. NAO serve para UTC-3: la o offset cru (-180) e Newfoundland
        e Brasilia e -181 — por isso o mapa explicito vem primeiro.
        """
        nome = str(template.get("timezone", "") or "")
        if nome in _TIMEZONE_IDS:
            return _TIMEZONE_IDS[nome]
        offset = template.get("timezone_offset_minutes")
        if offset is None:
            return _TIMEZONE_IDS["America/Sao_Paulo"]
        minutos = _sql_int(offset, -180)
        # -180 sem nome conhecido ainda e mais provavel ser Brasil que Newfoundland.
        return -181 if minutos == -180 else minutos

    @staticmethod
    def _lcd_language(template: dict[str, Any]) -> str:
        raw = str(template.get("lcd_language", "pt-BR") or "pt-BR").lower()
        return _LCD_LANGUAGES.get(raw, "pt_BR")

    @classmethod
    def _render_softkeys(
        cls, keys: list[dict[str, Any]], row: dict[str, Any], account: int,
    ) -> list[str]:
        """`function_keys` do ambiente -> UPDATE em TAB_SOFTKEY (PK 1..10).

        Mesma semantica dos outros adapters: `key`='LineKey2' -> indice 2; o
        valor vem da linha (`value_source='linha'` -> `row[value_field]`) ou e
        fixo. Tecla fora de 1..10 e ignorada (o 125i nao tem modulo de
        expansao; PK 11+ existe na tabela mas nao no aparelho).

        O `label` do ambiente NAO tem destino aqui: esta plataforma nao tem
        rotulo de tecla. `TAB_SOFTKEY.Number` parece candidato mas nao e — na
        tela de teclas o campo "Número" so fica editavel para BLF (Type=1) e
        Gravacao (Type=8) (`ng-disabled="Type != 1 && Type != 8"`), ou seja e
        um numero auxiliar da funcao, nao um nome. Gravamos `Number=''` para
        limpar sobra de uma configuracao anterior da mesma tecla.
        """
        out: list[str] = []
        for fk in keys:
            m = re.search(r"(\d+)$", str(fk.get("key", "")))
            if not m:
                continue
            pk = int(m.group(1))
            if not 1 <= pk <= _SOFTKEY_PK_MAX:
                log.warning(
                    "tip125i: tecla fora do alcance do aparelho, ignorada",
                    key=fk.get("key"), pk=pk, maximo=_SOFTKEY_PK_MAX,
                )
                continue
            tipo = _SOFTKEY_TYPES.get(str(fk.get("type", "disabled")).lower(), 0)
            if fk.get("value_source") == "linha":
                valor = str(row.get(fk.get("value_field") or "numero_abreviado", "") or "")
            else:
                valor = str(fk.get("value_fixed", "") or "")
            # `Account` da tecla: 0-based como o resto do firmware. Tipo 0
            # (nao definido) exige Auto — e o que a propria UI grava.
            if tipo == 0:
                acct: int = _SOFTKEY_ACCOUNT_AUTO
            else:
                acct = max(_sql_int(fk.get("account", 1), 1), 1) - 1
            out.append(
                f"UPDATE TAB_SOFTKEY SET Type={tipo},Value={_sql_str(valor, 'valor da tecla')},"
                f"Account={acct},Number='' WHERE PK = {pk};",
            )
        return out

    @staticmethod
    def _render_web_admin(template: dict[str, Any]) -> list[str]:
        """Troca a senha do admin web SO se o ambiente definiu `nova_web_password`.

        `TAB_SECURITY_ACCOUNT` e (SECAccount, SECPassword) em texto claro —
        `SECAccount` e a PK, entao trocamos a senha DO usuario informado e nunca
        criamos usuario novo. Sem `nova_web_password` a credencial fica intacta.
        """
        nova_pwd = str(template.get("nova_web_password", "") or "")
        if not nova_pwd:
            return []
        nova_user = str(template.get("nova_web_user", "") or "").strip() or "admin"
        return [
            f"UPDATE TAB_SECURITY_ACCOUNT SET SECPassword={_sql_str(nova_pwd, 'nova senha web')} "
            f"WHERE SECAccount = {_sql_str(nova_user, 'novo usuário web')};",
        ]

    # -------------------------------------------------------------- whitelist
    @staticmethod
    def _assert_whitelist(sql: str) -> None:
        """Reparsa o SQL gerado e aborta se sair da whitelist.

        Defesa em profundidade contra a regra de nunca tocar em rede: valida o
        VERBO (so UPDATE) e cada par `tabela.coluna` atribuido.
        """
        for raw in sql.splitlines():
            stmt = raw.strip()
            if not stmt or stmt.startswith("--"):
                continue
            m = _STATEMENT_RE.match(stmt)
            if not m:
                raise RuntimeError(
                    f"TIP 125i: statement nao reconhecido (so UPDATE): {stmt[:60]!r}",
                )
            tabela = m.group("tabela").upper()
            for col in _ASSIGN_RE.findall(m.group("sets")):
                chave = f"{tabela}.{col}"
                if chave not in _WHITELIST:
                    raise RuntimeError(
                        f"TIP 125i: coluna fora da whitelist (possivel rede!): {chave!r}",
                    )

    # ------------------------------------------------------------ send_config
    async def send_config(
        self, ip: str, creds: VendorCredentials, cfg: bytes, *, fmt: str = "sql",
    ) -> None:
        """Grava o SQL, avisa o firmware (`notify.cgi`) e REINICIA o controle de
        chamadas — as tres etapas sao obrigatorias.

        Sem o `notify` o valor fica so no banco e o aparelho segue operando com o
        antigo. E sem o `restart_control_call.cgi` o telefone **fica preso na
        sessao SIP anterior**: medido em bancada, ele continua registrado com a
        credencial velha mesmo depois do notify, e a conta nova nunca sobe. Pior,
        o caminho intuitivo nao resolve — desativar a conta
        (``AccountActive=0`` + notify) derruba o registro em 1 s, mas religar
        (``=1`` + notify, nas duas tabelas) **nao** faz voltar: o banco diz ativo
        e o aparelho fica fora do ar. Quem religa e o restart, que devolveu o
        registro em 2 s. E o mesmo endpoint que a web UI do telefone chama
        quando a versao do TLS muda.

        O restart reinicia SO a pilha de chamadas (nao o aparelho), mas derruba
        chamada em curso — daí `ActionResult.rebooted` nao se aplica aqui:
        `send_config` ja e uma operacao de janela de manutencao no pipeline.
        """
        if fmt not in ("sql", "xml"):  # 'xml' tolerado por compat de assinatura
            raise ValueError(f"TIP 125i: fmt deve ser 'sql', recebido {fmt!r}")
        sql = cfg.decode("utf-8")
        self._assert_whitelist(sql)  # defesa em profundidade tambem no envio

        async with self._client(ip, creds) as client:
            resp = await self._executar_com_retry(client, sql, ip)
            self._raise_for_db_error(resp, ip)
            await client.get(self._NOTIFY, params={"tables": ",".join(_NOTIFY_TABLES)})
            rebootou = await self._restart_call_control(client, ip)
        log.info(
            "tip125i: config aplicada",
            ip=ip, statements=sql.count(";"), reiniciou_aparelho=rebootou,
        )

    @classmethod
    async def _executar_com_retry(
        cls, client: httpx.AsyncClient, sql: str, ip: str,
    ) -> httpx.Response:
        """Reenvia o SQL quando o aparelho responde 401 com a credencial certa.

        Visto em campo com `admin`/`admin` confirmado no aparelho e o mesmo SQL
        passando segundos depois pela mesma credencial: **o 401 deste firmware
        nem sempre é sobre credencial**. Ele também sai quando o servidorzinho
        embarcado recusa a request antes de chegar ao CGI — foi o que medimos
        com Base64 cru contendo `+`/`/`, e é o mesmo código que aparece sob
        concorrência, logo após um reinício da pilha, ou numa aplicação em massa.

        Como um 401 vira `VendorAuthError`, ele faz o pipeline gastar a cadeia de
        credenciais e reportar "credencial recusada" — mandando o operador
        conferir uma senha que está certa. Então tentamos de novo antes de
        acreditar no 401. Se a credencial estiver mesmo errada, todas as
        tentativas falham e o erro sai igual (só ~2 s mais tarde).

        O retry é seguro porque cada statement é um `UPDATE` idempotente: grava
        o mesmo valor, no mesmo `WHERE`, quantas vezes for.
        """
        resp = await cls._execute(client, sql)
        for tentativa in range(1, cls._AUTH_RETRIES + 1):
            if resp.status_code not in (401, 403):
                return resp
            log.warning(
                "tip125i: 401/403 com a credencial informada — tentando de novo",
                ip=ip, tentativa=tentativa, de=cls._AUTH_RETRIES,
            )
            await asyncio.sleep(cls._AUTH_RETRY_DELAY_S)
            resp = await cls._execute(client, sql)
        return resp

    @classmethod
    async def _restart_call_control(cls, client: httpx.AsyncClient, ip: str) -> bool:
        """Poe a config nova em vigor. Devolve True se o APARELHO foi reiniciado.

        Sem este passo o telefone **fica preso na sessao SIP anterior**: segue
        registrado com a credencial velha e a conta nova nunca sobe. O
        `notify.cgi` nao resolve — no fw 4.3 ele nem sequer derruba a conta
        quando ela e desativada, ou seja, a pilha SIP simplesmente nao reage.

        Qual reinicio usar depende do firmware, e e por isso que a primeira
        versao desta correcao nao ajudou os aparelhos em campo: ela so conhecia
        o endpoint do fw 5.0.x, que responde **404** no 4.3.x. Entao tentamos o
        leve primeiro e caimos no pesado quando ele nao existe:

          * `/restart_control_call.cgi` (fw 5.0.x) reinicia so a pilha de
            chamadas — o aparelho nao reinicia e o registro volta em ~2 s.
          * `/restart.cgi` (fw 4.3.x) reinicia o aparelho inteiro, ~1 min fora
            do ar. E o unico caminho la: nao existe reinicio parcial.

        Nenhum dos dois costuma responder — ambos derrubam o processo que serve
        a propria request. Timeout aqui e o caminho feliz; tratar como erro
        faria toda aplicacao bem-sucedida ser reportada como falha.
        """
        try:
            resp = await client.post(cls._RESTART_CALL, timeout=cls._RESTART_TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            log.debug(
                "tip125i: reinicio da pilha de chamadas sem resposta (esperado)",
                ip=ip, erro=type(exc).__name__,
            )
            return False
        if resp.status_code != 404:
            return False

        # Firmware antigo: so o reboot completo aplica a conta.
        log.info(
            "tip125i: firmware sem reinicio parcial — reiniciando o aparelho "
            "para a conta SIP entrar em vigor",
            ip=ip,
        )
        try:
            await client.post(cls._RESTART_SYSTEM, timeout=cls._RESTART_TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            log.debug(
                "tip125i: reboot sem resposta (esperado)", ip=ip, erro=type(exc).__name__,
            )
        return True

    @classmethod
    async def _execute(cls, client: httpx.AsyncClient, sql: str) -> httpx.Response:
        """`GET /db.cgi?<base64(SQL)>`, com o Base64 PERCENT-ENCODADO.

        Aqui fazemos de proposito diferente da web UI do aparelho, que concatena
        o Base64 cru (`DB_DRIVER_URL+"?"+statement`). Medido em bancada: quando o
        Base64 contem `+` ou `/`, a query crua faz o servidor responder **401
        Unauthorized** — a request morre antes do CGI, e o sintoma (credencial
        recusada) nao tem nada a ver com a causa. Com `%2B`/`%2F` a mesma
        consulta passa: o CGI faz URL-decode antes do Base64. Ou seja, a propria
        tela do telefone quebra dependendo do conteudo que o operador digitou.

        Passar por `params` deixa o httpx encodar. O `=` que sobra no fim (o
        payload vai como NOME de parametro, entao a URL termina em `...%3D%3D=`)
        e ignorado pelo firmware — tambem comprovado em bancada.

        O `.strip()` nao e higiene: e correcao de bug do firmware. QUALQUER
        sobra depois do `;` do ultimo statement — uma nova linha, um espaco, um
        comentario `--` — faz o db.cgi responder **HTTP 200 com corpo vazio**:
        nada executa, nada e reportado, e quem chamou acha que aplicou. Foi
        assim que a primeira versao deste adapter "aplicou" uma config que o
        telefone nunca recebeu. O SQL tem de terminar exatamente em `;`.
        """
        payload = base64.b64encode(sql.strip().encode("utf-8")).decode("ascii")
        return await client.get(cls._DB, params={payload: ""})

    @staticmethod
    def _raise_for_db_error(resp: httpx.Response, ip: str) -> None:
        """O db.cgi devolve HTTP 200 mesmo com erro de SQL — quem checa somos nos."""
        if resp.status_code in (401, 403):
            raise VendorAuthError(f"TIP 125i {ip}: credencial recusada (HTTP {resp.status_code})")
        resp.raise_for_status()
        if not resp.text.strip():
            # 200 + corpo vazio = o firmware descartou o comando sem executar
            # (tipicamente sobra depois do `;` final — ver `_execute`). Silencio
            # aqui seria "aplicado com sucesso" sem nada ter sido aplicado.
            raise RuntimeError(
                f"TIP 125i {ip}: db.cgi respondeu vazio — o comando foi descartado "
                f"sem executar (SQL malformado para o firmware)",
            )
        try:
            data = json.loads(resp.text)
        except ValueError:
            raise RuntimeError(f"TIP 125i {ip}: resposta do db.cgi nao e JSON") from None
        for pos, item in enumerate(data if isinstance(data, list) else [data], start=1):
            if not isinstance(item, dict):
                continue
            if item.get("error"):
                raise RuntimeError(f"TIP 125i {ip}: {item['error']}")
            # `affected` conta as linhas que casaram com o WHERE (grava o mesmo
            # valor e ainda vem 1). Entao 0 significa que a linha nao existe —
            # conta SIP fora do alcance do aparelho, tecla inexistente, ou
            # `nova_web_user` que nao e um usuario cadastrado. O UPDATE nao faz
            # nada e, sem esta checagem, isso viraria "aplicado com sucesso".
            if item.get("affected") == 0:
                raise RuntimeError(
                    f"TIP 125i {ip}: o comando {pos} não encontrou o registro para "
                    f"atualizar (0 linhas). Verifique a conta SIP escolhida e, se "
                    f"estiver trocando a senha web, se o usuário existe no aparelho.",
                )

    # ----------------------------------------------------------- backup_config
    async def backup_config(self, ip: str, creds: VendorCredentials) -> bytes | None:
        """Backup nativo do aparelho (`/backup.cgi`).

        Vem cifrado pelo firmware (OpenSSL "Salted__"), so restauravel pela
        propria tela do telefone — guardamos como blob opaco.
        """
        try:
            async with self._client(ip, creds) as client:
                resp = await client.get(self._BACKUP)
            if resp.status_code in (401, 403):
                raise VendorAuthError(f"TIP 125i {ip}: credencial recusada no backup")
            resp.raise_for_status()
            return resp.content or None
        except VendorAuthError:
            raise
        except Exception as exc:  # backup e best-effort: nao derruba a aplicacao
            log.warning("tip125i: backup falhou", ip=ip, erro=str(exc))
            return None
