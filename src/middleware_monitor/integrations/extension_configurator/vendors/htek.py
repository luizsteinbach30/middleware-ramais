"""Adapter HTEK (telefones HanLong / HTEK — UC9xx, UC8xx, UC1xx, UC2xx).

Validado em lab 2026-05-19 contra IP 192.168.0.41 (modelo UC902G):
  GET http://<ip>/        → 401 + `Server: HanLong`  (fingerprint único)
  GET http://<ip>/index.htm  com Basic Auth admin/admin → 200 com tabela de status

A página de status renderiza valores em <td width="280"> imediatamente após
<td width="230"><script>document.write(jscs.LABEL);</script></td>. Capturamos
os pares (label, value) com regex DOTALL.

Campos relevantes para nós: product_type, firmware_version, mac_address.
Para firmware extraímos a versão ROM (ex: "2.42.6.5.45R14") de uma string
do tipo "BOOT--X<br>ROM--Y<br>DSP--Z".

Schema XML para provisionamento (validado contra cfg.xml exportado do
aparelho em 2026-05-19): elemento raiz <hl_provision version="1"> com
<config version="1"> dentro. Cada parâmetro é um elemento <Pnnn> com
atributo `para="LabelHumano"` e o valor como texto. Configs parciais
são aceitas — o aparelho aplica só os P-codes presentes.

P-codes críticos:
  Profile1 (servidor SIP):  P47 Sipserver · P130 SipTransport (0=UDP,1=TCP,2=TLS)
                            P31 SipRegistration (1=on) · P32 RegisterExpiration
                            P57..P62 codecs preferidos (0=PCMU,8=PCMA,9=G722,
                            2=G729,20=G726-32,120=Opus)
  Account1 (linha SIP):     P271 Active · P24082 Profile (0..5)
                            P20000 Label · P25110 Extension · P3 DispalyName
                            P35 SipUserId · P36 AuthenticateID · P34 AuthenticatePassword
  Preferências:             P64 TimeZone · P2525 WebLanguage · P8621 LcdLanguage
                            P30 NW_Adv_UrlOrIpAddress (NTP primário)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote as _url_quote
from xml.sax.saxutils import escape as xml_escape

import httpx

from .base import DiscoveryResult, VendorAdapter, VendorAuthError, VendorCredentials


# REGRA HTEK: o firmware faz URL-decode (%XX) no conteudo de texto dos XML
# enviados via /HLCFG_XML_configuration.htm antes de gravar nos P-codes.
# Confirmado lab 2026-05-21 (UC902G): senha com `%12` raw vira lixo (byte 0x12);
# escapada para `%2512` registra normalmente. Por isso TODO valor de texto que
# vai para o XML do HTEK precisa ser URL-encodado e depois XML-escapado.
def _htek_text(value: object) -> str:
    """URL-encode + XML-escape para conteudo de texto de qualquer P-code.

    Apos URL-encode, sobram so chars XML-safe (alfanum + %-_.~), entao o
    xml_escape e idempotente — mantido por defesa em profundidade.
    """
    return xml_escape(_url_quote(str(value or ""), safe=""))


_TEMPLATE_PATH = Path(__file__).parent / "htek_template.xml"

# Codec id por nome (P57..P62 do HTEK). Mapeamento extraído do cfg.xml real.
_CODEC_IDS: dict[str, int] = {
    "pcmu": 0, "g711u": 0, "g.711u": 0,
    "pcma": 8, "g711a": 8, "g.711a": 8,
    "g722": 9, "g.722": 9,
    "g729": 2, "g.729": 2,
    "g726": 20, "g726-32": 20,
    "opus": 120,
    "ilbc": 97,
}

_TIMEZONE_IDS: dict[str, int] = {
    # Apenas os mais usados pelo cliente; lista completa nos manuais HTEK.
    "America/Sao_Paulo": 18,  # GMT-3
    "America/Manaus": 14,     # GMT-4
    "America/Noronha": 25,    # GMT-2
    "UTC": 30,
}

_LANGUAGE_IDS: dict[str, int] = {
    # Conferir nos manuais por modelo — o id pode variar por firmware.
    "en": 0, "en-US": 0,
    "pt-BR": 21, "pt": 21,
    "es": 6, "es-ES": 6,
}

# Mapa LineKey → P-codes (Type/Mode/Value/Label/Account/Extension).
# Confirmado em cfg.xml real do UC902G para LineKey1..4. LineKey5+ usa esquema
# diferente (P20200+), implementar quando precisar.
_LINEKEY_PCODES: dict[str, dict[str, str]] = {
    "LineKey1": {
        "type": "P41200", "mode": "P20600", "value": "P41300",
        "label": "P41400", "account": "P41500", "extension": "P41600",
    },
    "LineKey2": {
        "type": "P41201", "mode": "P20601", "value": "P41301",
        "label": "P41401", "account": "P41501", "extension": "P41601",
    },
    "LineKey3": {
        "type": "P41202", "mode": "P20602", "value": "P41302",
        "label": "P41402", "account": "P41502", "extension": "P41602",
    },
    "LineKey4": {
        "type": "P41203", "mode": "P20603", "value": "P41303",
        "label": "P41403", "account": "P41503", "extension": "P41603",
    },
}

# Type IDs do HTEK (HanLong) para LineKey. Confirmar com manual se precisar de outros.
_FUNCTIONKEY_TYPE_IDS: dict[str, int] = {
    "disabled": 0,
    "line": 1,
    "speed_dial": 2,
    "blf": 3,
}


class HTEKAdapter(VendorAdapter):
    vendor_id = "htek"

    _FINGERPRINT_TIMEOUT = 3.0
    _FINGERPRINT_HEADER_VALUE = "hanlong"
    _DISCOVERY_TIMEOUT = 5.0
    _DISCOVERY_PATH = "/index.htm"

    _FIELD_PATTERN = re.compile(
        r"jscs\.([a-z_]+)\)\s*;?\s*</script>\s*</td>\s*<td[^>]*>(.*?)</td>",
        re.DOTALL,
    )
    _ROM_PATTERN = re.compile(r"ROM--([0-9][\w.\-]+)", re.IGNORECASE)
    # Padrão único do HTEK no body autenticado: chamadas jscs.* nos labels
    # (ver fixtures/htek_uc902g_index.html — firmware antigo e novo usam).
    _JSCS_PATTERN = re.compile(
        r"jscs\.(?:product_type|firmware_version|mac_address|statusstatu|account_status)",
    )

    @staticmethod
    def _pick_auth(www_authenticate: str, creds: VendorCredentials) -> httpx.Auth:
        """Escolhe Basic ou Digest baseado no header WWW-Authenticate do aparelho.

        Firmware antigo do HTEK (ex: UC902G 2.42.6.5.45R14) usa Basic.
        Firmware novo (ex: UC902G mais recente) passou a usar Digest sem
        header `Server: HanLong`.
        """
        if "digest" in www_authenticate.lower():
            return httpx.DigestAuth(creds.username, creds.password)
        return httpx.BasicAuth(creds.username, creds.password)

    async def _probe_and_auth(
        self, client: httpx.AsyncClient, ip: str, creds: VendorCredentials,
    ) -> httpx.Auth:
        """Descobre o auth scheme via GET / não-autenticado e devolve o auth pronto."""
        probe = await client.get(f"http://{ip}/")
        return self._pick_auth(probe.headers.get("WWW-Authenticate", ""), creds)

    async def fingerprint(
        self, ip: str, creds: VendorCredentials | None = None,
    ) -> float:
        """Confianca 0.0-1.0 de que o IP eh HTEK.

        Estratégia em camadas:
          1. GET /  — se vier `Server: HanLong`, é HTEK (firmware antigo). 1.0
          2. Se temos credenciais, autenticar /index.htm (escolhendo Basic ou
             Digest pelo WWW-Authenticate) e procurar padrões `jscs.*` no body.
             Confirma firmware novo. 0.95
          3. Sem credenciais e sem header HanLong → 0.0 (não dá pra ter certeza).
        """
        url = f"http://{ip}/"
        try:
            async with httpx.AsyncClient(timeout=self._FINGERPRINT_TIMEOUT) as client:
                resp = await client.get(url)
                server = resp.headers.get("Server", "").strip().lower()
                if self._FINGERPRINT_HEADER_VALUE in server:
                    return 1.0
                if creds is None:
                    return 0.0
                # firmware novo — confirma autenticando
                auth = self._pick_auth(resp.headers.get("WWW-Authenticate", ""), creds)
                authed = await client.get(
                    f"http://{ip}{self._DISCOVERY_PATH}",
                    auth=auth,
                    timeout=self._DISCOVERY_TIMEOUT,
                )
                if authed.status_code == 200 and self._JSCS_PATTERN.search(authed.text):
                    return 0.95
                return 0.0
        except httpx.HTTPError:
            return 0.0

    async def discover(self, ip: str, creds: VendorCredentials) -> DiscoveryResult:
        async with httpx.AsyncClient(timeout=self._DISCOVERY_TIMEOUT) as client:
            auth = await self._probe_and_auth(client, ip, creds)
            resp = await client.get(f"http://{ip}{self._DISCOVERY_PATH}", auth=auth)
        resp.raise_for_status()
        return self.parse_status_page(resp.text)

    @classmethod
    def parse_status_page(cls, html: str) -> DiscoveryResult:
        """Extrai campos da página /index.htm do HTEK.

        Separado em classmethod para poder testar sem rede.
        """
        fields: dict[str, str] = {}
        for label, value in cls._FIELD_PATTERN.findall(html):
            cleaned = re.sub(r"\s+", " ", value).strip()
            if cleaned and cleaned != "&nbsp;" and label not in fields:
                fields[label] = cleaned

        firmware: str | None = None
        fw_raw = fields.get("firmware_version")
        if fw_raw:
            rom_match = cls._ROM_PATTERN.search(fw_raw)
            firmware = rom_match.group(1) if rom_match else fw_raw

        return DiscoveryResult(
            vendor=cls.vendor_id,
            model=fields.get("product_type"),
            firmware=firmware,
            mac=fields.get("mac_address"),
            confidence=1.0 if fields.get("product_type") else 0.6,
            raw=fields,
        )

    @staticmethod
    def _codec_id(name_or_id: str | int) -> int:
        if isinstance(name_or_id, int):
            return name_or_id
        s = str(name_or_id).strip().lower()
        if s in _CODEC_IDS:
            return _CODEC_IDS[s]
        if s.isdigit():
            return int(s)
        raise ValueError(f"codec desconhecido: {name_or_id!r}")

    @staticmethod
    def _timezone_id(value: str | int) -> int:
        if isinstance(value, int):
            return value
        s = str(value).strip()
        if s in _TIMEZONE_IDS:
            return _TIMEZONE_IDS[s]
        if s.isdigit():
            return int(s)
        raise ValueError(f"timezone HTEK desconhecido: {value!r}")

    @staticmethod
    def _language_id(value: str | int) -> int:
        if isinstance(value, int):
            return value
        s = str(value).strip()
        if s in _LANGUAGE_IDS:
            return _LANGUAGE_IDS[s]
        if s.isdigit():
            return int(s)
        raise ValueError(f"language HTEK desconhecido: {value!r}")

    @classmethod
    def _render_function_keys(cls, keys: list[dict[str, Any]], row: dict[str, Any]) -> str:
        """Renderiza o bloco XML de FunctionKeys.

        Cada item de `keys` tem: key (LineKey1..4), type (line/speed_dial/blf/disabled),
        label, account (0..5 ou 255), value_source ("linha" | "fixo"),
        value_field (campo da linha quando source=linha), value_fixed.

        Se value_source=linha, o valor lido de row[value_field] é o que vai como
        Value (ex: numero_abreviado=9999). Falha silenciosa se a key não estiver
        em _LINEKEY_PCODES (loga, mas não quebra a geração).
        """
        if not keys:
            return ""
        lines: list[str] = []
        for k in keys:
            key_name = k.get("key", "")
            pcodes = _LINEKEY_PCODES.get(key_name)
            if not pcodes:
                continue  # ignora silenciosamente; UI deve validar
            type_raw = str(k.get("type", "disabled")).strip().lower()
            type_id = _FUNCTIONKEY_TYPE_IDS.get(type_raw)
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
            # HTEK: o aparelho usa account=0 para "Account1" nas softkeys.
            # Valores diferentes apontam para um perfil inexistente e a tecla
            # nao disca. (Intelbras eh diferente — ver intelbras.py.)
            account = 0
            lines.append(
                f'        <{pcodes["type"]} para="{key_name}_Type">{type_id}</{pcodes["type"]}>'
            )
            lines.append(
                f'        <{pcodes["value"]} para="{key_name}_Value">{_htek_text(value)}</{pcodes["value"]}>'
            )
            lines.append(
                f'        <{pcodes["label"]} para="{key_name}_Label">{_htek_text(label)}</{pcodes["label"]}>'
            )
            lines.append(
                f'        <{pcodes["account"]} para="{key_name}_Account">{account}</{pcodes["account"]}>'
            )
        return "\n".join(lines)

    def generate_config(self, template: dict[str, Any], row: dict[str, Any]) -> bytes:
        """Gera arquivo de configuração XML HTEK (formato hl_provision v1).

        Usa o template `htek_template.xml` enxuto. Aceita configs parciais —
        o aparelho preserva valores não-listados.

        Parâmetros esperados:
          template: campos compartilhados do modelo padrão (servidor SIP, codecs,
                    timezone, idioma, NTP). Aceita strings amigáveis (ex: "g722",
                    "pt-BR", "America/Sao_Paulo") que serão convertidas em P-codes.
          row:      dados específicos do telefone — conta_sip, senha_sip,
                    servidor_sip (override), label, display_name, extension.

        Valor "" é renderizado como texto vazio no XML (compatível com cfg.xml
        real visto no UC902G).
        """
        codecs_raw = template.get("codecs", ["g722", "pcma", "pcmu", "g729"])
        codec_ids = [self._codec_id(c) for c in codecs_raw]
        codec_ids = (codec_ids + [0] * 6)[:6]  # pad até 6 com PCMU

        sip_transport_raw = template.get("sip_transport", "udp")
        sip_transport = {"udp": 0, "tcp": 1, "tls": 2}.get(
            str(sip_transport_raw).lower(), int(sip_transport_raw) if str(sip_transport_raw).isdigit() else 0,
        )

        account_active_raw = row.get("account_active", template.get("account_active", 1))
        account_active = 1 if account_active_raw in (1, "1", True, "true", "on") else 0

        ctx: dict[str, str] = {
            "account_active": str(account_active),
            "sip_server": _htek_text(row.get("servidor_sip") or template.get("sip_server", "")),
            "sip_transport": str(sip_transport),
            "register_expiration": str(template.get("register_expiration", 15)),
            "codec_1": str(codec_ids[0]),
            "codec_2": str(codec_ids[1]),
            "codec_3": str(codec_ids[2]),
            "codec_4": str(codec_ids[3]),
            "codec_5": str(codec_ids[4]),
            "codec_6": str(codec_ids[5]),
            "label": _htek_text(row.get("label") or row.get("conta_sip", "")),
            "extension": _htek_text(row.get("extension") or row.get("conta_sip", "")),
            "user_id": _htek_text(row.get("conta_sip", "")),
            "auth_id": _htek_text(row.get("auth_id") or row.get("conta_sip", "")),
            "password": _htek_text(row.get("senha_sip", "")),
            "display_name": _htek_text(row.get("display_name") or row.get("conta_sip", "")),
            "timezone": str(self._timezone_id(template.get("timezone", "America/Sao_Paulo"))),
            "web_language": str(self._language_id(template.get("web_language", "pt-BR"))),
            "lcd_language": str(self._language_id(template.get("lcd_language", "pt-BR"))),
            "ntp_server": _htek_text(template.get("ntp_server", "a.ntp.br")),
            "function_keys_xml": self._render_function_keys(
                template.get("function_keys", []) or [], row,
            ),
            "web_admin_xml": self._render_web_admin(template),
        }
        return _TEMPLATE_PATH.read_text(encoding="utf-8").format_map(ctx).encode("utf-8")

    @staticmethod
    def _render_web_admin(template: dict[str, Any]) -> str:
        """Renderiza P-codes de credencial web admin SOMENTE se foi configurada
        uma nova credencial no ambiente. Sem isso, nao toca nas senhas atuais
        do aparelho.

        P-codes:
          P8681 LogUser_Admin    — usuario admin (geralmente fixo "admin")
          P2    AdminPassword     — senha admin
        """
        nova_user = str(template.get("nova_web_user", "") or "").strip()
        nova_pwd = str(template.get("nova_web_password", "") or "")
        if not nova_pwd and not nova_user:
            return ""
        lines: list[str] = []
        if nova_user:
            lines.append(f'        <P8681 para="LogUser_Admin">{_htek_text(nova_user)}</P8681>')
        if nova_pwd:
            lines.append(f'        <P2 para="AdminPassword">{_htek_text(nova_pwd)}</P2>')
        return "\n".join(lines)

    _UPLOAD_XML_PATH = "/HLCFG_XML_configuration.htm"
    _UPLOAD_BIN_PATH = "/HLCFG_BIN_configuration.htm"

    async def send_config(
        self,
        ip: str,
        creds: VendorCredentials,
        cfg: bytes,
        *,
        fmt: str = "xml",
    ) -> None:
        """Detecta Basic vs Digest automaticamente via probe sem auth, depois envia.

        Não exige fingerprint prévio — assume que o ambiente já sabe que o
        aparelho é HTEK (modelo cadastrado).
        """
        """Envia config via upload na web GUI (configuration.htm).

        Validado em lab 2026-05-19 — formulários de upload em
        http://192.168.0.41/configuration.htm:

          <form enctype="multipart/form-data" method=POST
                action="/HLCFG_XML_configuration.htm">
            <input type="file" name="CfgXmlFile">

          <form enctype="multipart/form-data" method=POST
                action="/HLCFG_BIN_configuration.htm">
            <input type="file" name="CfgFile">

        Por padrão usamos XML (formato cfgXXXXXXXXXX.xml HTEK). Use fmt="bin"
        para o blob binário (backup completo do telefone).

        TODO: o aparelho normalmente reinicia após aceitar a config. Confirmar
        comportamento e adicionar espera + healthcheck pós-upload.
        TODO: schema XML específico do HTEK (chaves P-codes) a definir após
        baixar um config existente do aparelho como referência.
        """
        if fmt == "xml":
            path = self._UPLOAD_XML_PATH
            field = "CfgXmlFile"
            filename = "config.xml"
        elif fmt == "bin":
            path = self._UPLOAD_BIN_PATH
            field = "CfgFile"
            filename = "config.bin"
        else:
            raise ValueError(f"fmt deve ser 'xml' ou 'bin', recebido: {fmt!r}")

        async with httpx.AsyncClient(timeout=15.0) as client:
            auth = await self._probe_and_auth(client, ip, creds)
            resp = await client.post(
                f"http://{ip}{path}",
                auth=auth,
                files={field: (filename, cfg, "application/octet-stream")},
            )
            if resp.status_code in (401, 403):
                raise VendorAuthError(
                    f"HTEK: login recusado (HTTP {resp.status_code})"
                )
            resp.raise_for_status()
