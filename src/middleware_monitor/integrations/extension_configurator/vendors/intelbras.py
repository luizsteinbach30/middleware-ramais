"""Adapter Intelbras V-series (V3001, V3101, V3501, V5501).

Validado em lab 2026-05-20 contra IP 192.168.0.154 (modelo V5501):
  Server: RapidLogic/1.1                                  (fingerprint unico)
  Bug do firmware: declara Transfer-Encoding: chunked mas envia body cru
  -> forcar HTTP/1.0 em todas as requests (httpx.AsyncClient(http1=True))

Auth (different do HTEK):
  1. GET /                                                cookie auth=<16 hex chars>
  2. GET /key==nonce?now=<ts>                             body = 16 chars (igual ao cookie)
  3. POST / com body URL-encoded:
        encoded=<user>:<md5_hash_de_encode_fn>
        CurLanguage=pt
        ReturnPage=/
     A fn encode() vive num <script> inline da pagina de login.
     [TODO 2026-05-20] descobrir o algoritmo exato (ver _hash_for_login)

Upload de config:
  POST /config.htm  multipart/form-data
    file field: CONFIG (nome do <input type=file>)

Backup nativo (download da config atual):
  GET /default_user_config.xml   (formato sysConf v2.0)
  GET /default_user_config.txt   (formato chave=valor)

Schema XML enviado:
  <sysConf>
    <sip>                     <line index='N'>  (linhas SIP — campos
                                   RegisterAddr/User/Pswd/PhoneNumber/DisplayName/SipName)
    <web>                     <account index='1'><Name>/<Password>  (web admin)
    <phone>                   <MenuPassword> + <KeyLockPassword>
    <dsskey>                  <dssSoft index='N'><Type/Value/Title/ICON>
  </sysConf>

Whitelist rigida (REGRA INVIOLAVEL — feedback-nunca-tocar-em-rede):
  PROIBIDO emitir: <net>, <vpn>, <dot1x>, <qos>, <hotspot>,
                   <web><WebPort/HttpsWebPort/TelnetPort/RemoteControl>.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, ClassVar
from xml.sax.saxutils import escape as xml_escape

import httpx

from .base import DiscoveryResult, VendorAdapter, VendorCredentials

# Aspas em campos de senha podem corromper o valor armazenado no aparelho
# (mesmo padrao visto no HTEK). Escapamos por seguranca.
_PASSWORD_ENTITIES = {'"': "&quot;", "'": "&apos;"}


def _xml_escape_password(s: str) -> str:
    return xml_escape(s, _PASSWORD_ENTITIES)


_TEMPLATE_PATH = Path(__file__).parent / "intelbras_template.xml"


def _md5(s: str) -> str:
    # Protocolo de login do Intelbras (firmware Rapid Logic) usa MD5 — uso de
    # hash legado, NAO para seguranca. Mantemos por compatibilidade.
    return hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()


class IntelbrasAdapter(VendorAdapter):
    vendor_id = "intelbras"

    _TIMEOUT = 8.0
    _FINGERPRINT_HEADER_VALUE = "rapidlogic"

    # ------------------------------------------------------------------
    # Client factory: TODOS os requests devem forçar HTTP/1.0 + connection close
    # por causa do bug de chunked do firmware Rapid Logic.
    # ------------------------------------------------------------------
    @staticmethod
    def _client(timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            http1=True, http2=False,
            timeout=timeout or IntelbrasAdapter._TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0", "Connection": "close"},
        )

    # ------------------------------------------------------------------
    # Fingerprint
    # ------------------------------------------------------------------
    async def fingerprint(self, ip: str, creds: VendorCredentials | None = None) -> float:
        try:
            async with self._client(timeout=3.0) as cli:
                resp = await cli.get(f"http://{ip}/")
        except httpx.HTTPError:
            return 0.0
        server = resp.headers.get("Server", "").strip().lower().replace(" ", "")
        if self._FINGERPRINT_HEADER_VALUE in server:
            return 1.0
        return 0.0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    @staticmethod
    def _hash_for_login(user: str, password: str, nonce: str) -> str:
        """Calcula o hash do POST de login do Intelbras.

        Algoritmo descoberto em 2026-05-20 lendo a funcao `encode()` do
        login.html (V5501 firmware Rapid Logic). Codigo original JS:

            function encode(nonce) {
                document.getElementById("encoded").value =
                    document.getElementById("username").value + ":" +
                    md5(username + ":" + password + ":" + nonce);
                ...
            }

        Retorna hex MD5 32 chars (o `username:` final eh anexado pelo caller).
        """
        return _md5(f"{user}:{password}:{nonce}")

    # Firmware Rapid Logic eh sensivel: muitas sessoes em curto intervalo
    # derrubam o servico HTTP do aparelho (mesmo respondendo ping).
    # Defaults conservadores; usuario pode subir o delay em produçao.
    _LOGIN_MAX_TRIES = 5
    _LOGIN_RETRY_DELAY_S = 4.0

    async def _login(self, ip: str, creds: VendorCredentials) -> httpx.AsyncClient:
        """Faz login e devolve um httpx.AsyncClient com a sessao autenticada.

        O firmware Rapid Logic eh flaky: as vezes o nonce retorna vazio se
        ainda tem sessao concorrente ativa ou o servidor esta digerindo
        a request anterior. Faz retry com pequeno delay.
        """
        import asyncio
        last_err: Exception | None = None
        for attempt in range(self._LOGIN_MAX_TRIES):
            cli = self._client()
            try:
                await cli.get(f"http://{ip}/")
                nresp = await cli.get(f"http://{ip}/key==nonce?now={attempt}")
                nonce = nresp.text[:16]
                if not nonce:
                    last_err = RuntimeError("nonce vazio (sessao concorrente ou flaky)")
                    await cli.aclose()
                    await asyncio.sleep(self._LOGIN_RETRY_DELAY_S)
                    continue
                h = self._hash_for_login(creds.username, creds.password, nonce)
                resp = await cli.post(
                    f"http://{ip}/",
                    data={"encoded": f"{creds.username}:{h}", "CurLanguage": "pt", "ReturnPage": "/"},
                )
                if "logon_content" in resp.text or "XSTR_HLP_AUTH_ERROR" in resp.text:
                    await cli.aclose()
                    raise RuntimeError("Intelbras: login recusado (user/senha errados)")
                return cli
            except httpx.HTTPError as exc:
                last_err = exc
                await cli.aclose()
                await asyncio.sleep(self._LOGIN_RETRY_DELAY_S)
        raise RuntimeError(
            f"Intelbras login falhou apos {self._LOGIN_MAX_TRIES} tentativas: {last_err}"
        )

    # ------------------------------------------------------------------
    # Discover: pega modelo/firmware/MAC da pagina /information.htm
    # ------------------------------------------------------------------
    async def discover(self, ip: str, creds: VendorCredentials) -> DiscoveryResult:
        cli = await self._login(ip, creds)
        try:
            resp = await cli.get(f"http://{ip}/information.htm")
        finally:
            await cli.aclose()
        resp.raise_for_status()
        return self.parse_information_page(resp.text)

    @classmethod
    def parse_information_page(cls, html: str) -> DiscoveryResult:
        """Extrai modelo, firmware, MAC de /information.htm.

        Validado em V5501 firmware 2.2.29: a pagina tem tabelas com
        `<span id="XSTR_LBL_INFO_xxx">Label</span>:</td><td>VALOR</td>`.

        Para MAC, o valor pode vir seguido de uma tag <font> de invalid:
        `<td>44:3b:32:4c:df:78<font ...>(...)</font></td>` — extraimos
        somente o prefixo antes do <font>.
        """
        def find(label_id: str) -> str | None:
            pat = rf'XSTR_LBL_INFO_{label_id}.*?</td>\s*<td[^>]*>(.*?)</td>'
            m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
            if not m:
                return None
            raw = m.group(1)
            # remove tudo a partir do primeiro tag '<' (descarta <font>(invalid)</font>)
            raw = raw.split("<", 1)[0]
            return re.sub(r"\s+", " ", raw).strip() or None
        model = find("MODEL")
        firmware = find("SW")
        mac = find("MAC")
        # MAC valido tem 17 chars no formato XX:XX:XX:XX:XX:XX
        if mac and not re.fullmatch(r"[0-9a-fA-F:]{17}", mac):
            mac = None
        return DiscoveryResult(
            vendor=cls.vendor_id, model=model, firmware=firmware, mac=mac,
            confidence=1.0 if model else 0.6,
            raw={"model": model, "firmware": firmware, "mac": mac},
        )

    # ------------------------------------------------------------------
    # generate_config
    # ------------------------------------------------------------------
    def generate_config(self, template: dict[str, Any], row: dict[str, Any]) -> bytes:
        """Gera o XML <sysConf> minimo com somente os campos editaveis.

        Por design o aparelho aplica configs parciais — campos nao listados
        ficam intactos. So emitimos: <sip><line index="1">, <web><account>,
        <phone>MenuPassword/KeyLockPassword, e <dsskey><dssSoft>.

        IMPORTANTE: NAO emitir <net> nem qualquer outra config de rede.
        Validado por whitelist em tests/unit/test_intelbras.py.
        """
        register_addr = str(row.get("servidor_sip") or template.get("sip_server", ""))
        register_user = str(row.get("auth_id") or row.get("conta_sip", ""))
        register_pwd = str(row.get("senha_sip", ""))
        phone_number = str(row.get("conta_sip", ""))
        display_name = str(row.get("display_name") or row.get("label") or phone_number)

        # Renderiza FunctionKeys -> dssSoft do Intelbras (Type:Speed Dial=1, BLF=2)
        dss_xml = self._render_function_keys(template.get("function_keys", []) or [], row)

        menu_password = str(template.get("menu_password", "123"))
        keylock_password = str(template.get("keylock_password", menu_password))
        # Defaults universais Intelbras: bloqueio de menu HABILITADO (2)
        # com timeout de 30s. O usuário pode sobrescrever no ambiente.
        keylock_enable = int(template.get("keylock_enable", 2))
        keylock_timeout = int(template.get("keylock_timeout", 30))

        ctx = {
            "register_addr": xml_escape(register_addr),
            "register_user": xml_escape(register_user),
            "register_pwd": _xml_escape_password(register_pwd),
            "phone_number": xml_escape(phone_number),
            "display_name": xml_escape(display_name),
            "sip_name": xml_escape(register_user),
            "menu_password": xml_escape(menu_password),
            "keylock_password": xml_escape(keylock_password),
            "keylock_enable": keylock_enable,
            "keylock_timeout": keylock_timeout,
            "dss_keys_xml": dss_xml,
            "web_admin_xml": self._render_web_admin(template),
        }
        return _TEMPLATE_PATH.read_text(encoding="utf-8").format_map(ctx).encode("utf-8")

    @staticmethod
    def _render_web_admin(template: dict[str, Any]) -> str:
        """Renderiza <web><account> SOMENTE se foi configurada uma nova
        credencial no ambiente. Sem isso, nao toca na senha web do aparelho."""
        nova_user = str(template.get("nova_web_user", "") or "").strip()
        nova_pwd = str(template.get("nova_web_password", "") or "")
        if not nova_pwd and not nova_user:
            return ""
        # Se so um foi preenchido, completar com defaults seguros
        if nova_user and not nova_pwd:
            return ""  # sem senha nao mexemos
        if not nova_user:
            nova_user = "admin"
        return (
            f'    <web>\n'
            f'        <account index="1">\n'
            f'            <Name>{xml_escape(nova_user)}</Name>\n'
            f'            <Password>{_xml_escape_password(nova_pwd)}</Password>\n'
            f'            <Level>10</Level>\n'
            f'        </account>\n'
            f'    </web>'
        )

    # Mapeamento dos tipos de DSS para Intelbras V-series (validado em V5501).
    # Type=1 eh "Tecla de memoria" no UI — o "subtipo" (Discagem rapida, BLF, etc)
    # vai EMBUTIDO no Value como sufixo: <numero>@<account>/<sufixo>
    # Validado 2026-05-21 com backup XML do user (config-com-dss-e-senha.xml):
    #   Subtipo "Discagem rapida" -> Value=`800@1/f` (Title=Central, Type=1)
    # Type=2 eh "Conta" (Line) — Value=SIP1/SIP2 sem sufixo.
    _DSS_TYPE_IDS: ClassVar[dict[str, int]] = {
        "disabled": 0,
        "speed_dial": 1,   # Memory Key + subtype Speed Dial
        "line": 2,
        "blf": 3,          # Memory Key + subtype BLF (sufixo ainda nao validado)
    }

    # Sufixo embutido no Value de Type=1 (Memory Key) por subtipo.
    # Default: speed_dial -> "/f" (Discagem rapida). Outros pendentes de
    # validacao em hardware (BLF, Intercom, etc).
    _DSS_VALUE_SUFFIX: ClassVar[dict[str, str]] = {
        "speed_dial": "/f",
    }

    @classmethod
    def _render_function_keys(cls, keys: list[dict[str, Any]], row: dict[str, Any]) -> str:
        """Renderiza o bloco <dsskey><internal index='1'><Fkey index='N'>...

        IMPORTANTE: <Fkey> sao as teclas FISICAS DSS (botoes hardware ao lado
        da tela). Nao confundir com <dssSoft> que sao as soft keys (touch da
        tela). Validado em V5501 2026-05-20: o usuario configurou Fkey 2 e
        nosso XML antigo (com dssSoft) nao tinha efeito.

        Cada item tem: key ('LineKey2' p.ex.; convertemos para index 2),
        type, label, value_source ('linha'|'fixo'), value_field, value_fixed.
        """
        if not keys:
            return ""
        lines = ["        <dsskey>", '            <internal index="1">']
        for k in keys:
            key_name = str(k.get("key", "LineKey1"))
            m = re.search(r"(\d+)$", key_name)
            if not m:
                continue
            idx = int(m.group(1))
            type_raw = str(k.get("type", "disabled")).strip().lower()
            type_id = cls._DSS_TYPE_IDS.get(type_raw)
            if type_id is None:
                if type_raw.isdigit():
                    type_id = int(type_raw)
                else:
                    raise ValueError(f"FunctionKey type desconhecido: {k.get('type')!r}")
            source = k.get("value_source", "fixo")
            if source == "linha":
                field = k.get("value_field") or "numero_abreviado"
                value = str(row.get(field, "") or "")
            else:
                value = str(k.get("value_fixed", "") or "")
            label = str(k.get("label", "") or "")
            # Para Memory Key (Type=1), o subtipo do UI vai EMBUTIDO no Value:
            #   `<numero>@<account>/<sufixo>` (ex.: 800@1/f para Discagem rapida).
            # Sem isso, a tecla aparece no UI como "Subtipo: Nenhum" e nao discaria.
            suffix = cls._DSS_VALUE_SUFFIX.get(type_raw, "")
            if type_id == 1 and value and suffix:
                account = int(k.get("account", 1) or 1)
                value = f"{value}@{account}{suffix}"
            lines.append(f'                <Fkey index="{idx}">')
            lines.append(f"                    <Type>{type_id}</Type>")
            lines.append(f"                    <Value>{xml_escape(value)}</Value>")
            lines.append(f"                    <Title>{xml_escape(label)}</Title>")
            lines.append('                    <ICON>Green</ICON>')
            lines.append("                </Fkey>")
        lines.append("            </internal>")
        lines.append("        </dsskey>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # send_config
    # ------------------------------------------------------------------
    _UPLOAD_PATH = "/config.htm"
    _UPLOAD_FIELD = "CONFIG"

    async def send_config(
        self, ip: str, creds: VendorCredentials, cfg: bytes, *, fmt: str = "xml",
    ) -> None:
        """Envia XML para /config.htm como multipart, autenticado.

        O aparelho normalmente reinicia apos aceitar a config.
        """
        if fmt != "xml":
            raise ValueError(f"Intelbras adapter so aceita fmt='xml', recebido: {fmt!r}")
        cli = await self._login(ip, creds)
        try:
            resp = await cli.post(
                f"http://{ip}{self._UPLOAD_PATH}",
                files={self._UPLOAD_FIELD: ("config.xml", cfg, "application/xml")},
                timeout=20.0,
            )
            resp.raise_for_status()
        finally:
            await cli.aclose()

    # ------------------------------------------------------------------
    # backup_config: usa o endpoint nativo de export
    # ------------------------------------------------------------------
    async def backup_config(self, ip: str, creds: VendorCredentials) -> bytes | None:
        try:
            cli = await self._login(ip, creds)
        except (httpx.HTTPError, RuntimeError):
            return None
        try:
            resp = await cli.get(f"http://{ip}/default_user_config.xml", timeout=15.0)
            if resp.status_code == 200 and resp.content:
                return resp.content
        except httpx.HTTPError:
            return None
        finally:
            await cli.aclose()
        return None
