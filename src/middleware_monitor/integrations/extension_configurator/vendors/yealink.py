"""Adapter Yealink (T3x/T4x — engenharia validada contra T31G fw 124.86.104.1).

Bancada 2026-05-27, IP 192.168.0.173, admin/admin. Descoberta completa do
fluxo de login e import de configuracao (ver scripts/yl_probe.py no
autocfg-ramais e a nota [[Yealink T31G - Homologacao]] no vault).

Particularidades (diferentes dos outros adapters):
  - SO HTTPS. A porta 80 e recusada; o aparelho serve em 443 com cert
    self-signed -> cliente precisa `verify=False`.
  - Login com senha cifrada em RSA NO CLIENTE (PKCS#1 v1.5):
      1. GET /servlet?m=mod_listener&p=login&q=loginForm&jumpto=status
         -> a pagina embute g_rsa_n (modulo hex, 1024-bit) e g_rsa_e (expoente,
            normalmente 010001).
      2. pwd = hex(RSA_PKCS1v15_encrypt(senha, (n,e)))
      3. POST /servlet?m=mod_listener&p=login&q=login  body username=..&pwd=..
         -> body contem {"authstatus":"done"} e Set-Cookie JSESSIONID.
  - Token CSRF: `g_strToken` (16-17 hex) renderizado em QUALQUER pagina
    autenticada (ex.: settings-config). Obrigatorio no POST de upload.

Import de configuracao (o nosso "send"):
  POST /servlet?m=mod_res&p=upload&type=localcfg&token=<g_strToken>
    multipart/form-data, campo de arquivo: `UploadName` (nome ex.: config.cfg)
  Resposta JSON em <div id="_RES_INFO_">: {"type":"localcfg","result":"<r>"}
    result: "success" (ok) · "noparam" (nenhum parametro reconhecido) ·
            "large" (arquivo > 5MB) · "failed"/"false" (erro).
  `type=localcfg` importa o .cfg key=value (MAC-all.cfg) e aplica PARCIALMENTE
  (so as chaves presentes), por isso e seguro para a regra "nunca tocar em rede".

Fingerprint:
  GET https://{ip}/ -> 302 Location contendo `m=mod_listener&p=login`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .base import (
    ACTION_NORMALIZE,
    ActionResult,
    DiscoveryResult,
    VendorActionUnsupported,
    VendorAdapter,
    VendorAuthError,
    VendorCredentials,
)

_TEMPLATE_PATH = Path(__file__).parent / "yealink_template.cfg"

# Prefixos de chave que ESTE adapter pode emitir no .cfg. Defesa em
# profundidade: generate_config valida cada linha contra esta lista. NENHUM
# prefixo de rede pode entrar aqui (ver test_yealink.py).
_ALLOWED_PREFIXES: tuple[str, ...] = (
    "account.1.",
    "account.2.",
    "linekey.",
    "local_time.",
    "security.user_password",  # troca de senha do admin web (credencial, nao rede)
)

# Yealink: transport_type da conta SIP.
_TRANSPORT_IDS: dict[str, int] = {"udp": 0, "tcp": 1, "tls": 2, "dns": 3}

# Fuso -> valor de local_time.time_zone do Yealink (offset em horas).
_TIMEZONE_OFFSETS: dict[str, str] = {
    "America/Sao_Paulo": "-3",
    "America/Manaus": "-4",
    "America/Noronha": "-2",
    "America/Rio_Branco": "-5",
    "UTC": "0",
}

# Tipos de linekey do Yealink (subset usado). Confirmado em hardware:
# 13 = Discagem Rapida (Speed Dial) — config real do usuario (value=800,Central).
# Os demais sao do manual Yealink; refinar BLF na bancada se necessario.
_LINEKEY_TYPE_IDS: dict[str, int] = {
    "disabled": 0, "na": 0, "none": 0,
    "line": 15,
    "speed_dial": 13, "discagem_rapida": 13,
    "blf": 16,
    "blf_list": 17,
    "forward": 2,
    "transfer": 3,
}


class YealinkAdapter(VendorAdapter):
    vendor_id = "yealink"

    _TIMEOUT = 10.0
    _LOGIN_FORM = "/servlet?m=mod_listener&p=login&q=loginForm&jumpto=status"
    _LOGIN_POST = "/servlet?m=mod_listener&p=login&q=login"
    _TOKEN_PAGE = "/servlet?m=mod_data&p=settings-config&q=load"
    _UPLOAD_PATH = "/servlet?m=mod_res&p=upload&type=localcfg"

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _client(timeout: float | None = None) -> httpx.AsyncClient:
        # verify=False: o aparelho so fala HTTPS com cert self-signed na LAN —
        # nao ha CA para validar (provisionamento local, mesma confianca de rede
        # dos adapters http://). follow_redirects=False p/ enxergar o 302 do
        # fingerprint.
        return httpx.AsyncClient(
            verify=False,  # noqa: S501 - cert self-signed do telefone, provisionamento LAN
            timeout=timeout or YealinkAdapter._TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"},
        )

    @staticmethod
    def _rsa_encrypt_hex(password: str, n_hex: str, e_hex: str) -> str:
        """RSA PKCS#1 v1.5 da senha -> hex (replica YLForm.RSAEncrypt/jsbn)."""
        n = int(n_hex, 16)
        e = int(e_hex, 16)
        pub = rsa.RSAPublicNumbers(e, n).public_key()
        ct = pub.encrypt(password.encode("utf-8"), padding.PKCS1v15())
        return ct.hex()

    # ------------------------------------------------------------ fingerprint
    async def fingerprint(self, ip: str, creds: VendorCredentials | None = None) -> float:
        try:
            async with self._client(timeout=4.0) as cli:
                resp = await cli.get(f"https://{ip}/")
        except httpx.HTTPError:
            return 0.0
        loc = resp.headers.get("Location", "")
        if resp.status_code in (301, 302) and "m=mod_listener&p=login" in loc:
            return 1.0
        return 0.0

    # -------------------------------------------------------------------- auth
    async def _login(self, ip: str, creds: VendorCredentials) -> httpx.AsyncClient:
        """Loga e devolve um client autenticado (cookie JSESSIONID no jar)."""
        cli = self._client()
        try:
            page = (await cli.get(f"https://{ip}{self._LOGIN_FORM}")).text
            n_m = re.search(r'g_rsa_n\s*=\s*"([0-9A-Fa-f]+)"', page)
            e_m = re.search(r'g_rsa_e\s*=\s*"([0-9A-Fa-f]+)"', page)
            if not (n_m and e_m):
                await cli.aclose()
                raise RuntimeError("Yealink: chave RSA (g_rsa_n/g_rsa_e) nao encontrada")
            enc = self._rsa_encrypt_hex(creds.password, n_m.group(1), e_m.group(1))
            resp = await cli.post(
                f"https://{ip}{self._LOGIN_POST}",
                content=f"username={creds.username}&pwd={enc}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if '"authstatus":"done"' not in resp.text.lower().replace(" ", ""):
                await cli.aclose()
                raise VendorAuthError(
                    f"Yealink: login recusado ({resp.text.strip()[:120]})"
                )
            return cli
        except httpx.HTTPError as exc:
            await cli.aclose()
            raise RuntimeError(f"Yealink: falha de rede no login: {exc}") from exc

    async def _get_token_and_rsa(
        self, ip: str, cli: httpx.AsyncClient,
    ) -> tuple[str, str, str]:
        """Le g_strToken (CSRF) + chave RSA (g_rsa_n/g_rsa_e) de pagina autenticada.

        A chave RSA e necessaria para cifrar o `maxlength` do upload (ver
        send_config) — o mesmo esquema PKCS#1 v1.5 do login.
        """
        page = (await cli.get(f"https://{ip}{self._TOKEN_PAGE}")).text
        tok = re.search(r'g_strToken\s*=\s*"([0-9A-Fa-f]+)"', page)
        n = re.search(r'g_rsa_n\s*=\s*"([0-9A-Fa-f]+)"', page)
        e = re.search(r'g_rsa_e\s*=\s*"([0-9A-Fa-f]+)"', page)
        if not (tok and n and e):
            raise RuntimeError("Yealink: g_strToken/chave RSA nao encontrados")
        return tok.group(1), n.group(1), e.group(1)

    # --------------------------------------------------------------- discover
    async def discover(self, ip: str, creds: VendorCredentials) -> DiscoveryResult:
        cli = await self._login(ip, creds)
        try:
            html = (await cli.get(f"https://{ip}/servlet?m=mod_data&p=status&q=load")).text
        finally:
            await cli.aclose()
        return self.parse_status_page(html)

    @classmethod
    def parse_status_page(cls, html: str) -> DiscoveryResult:
        """Extrai modelo/firmware/MAC da pagina de status (best-effort)."""
        def grab(*pats: str) -> str | None:
            for p in pats:
                m = re.search(p, html, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
            return None

        model = grab(r'g_strModel\s*=\s*"([^"]+)"', r'"device_model"\s*:\s*"([^"]+)"')
        firmware = grab(r'g_strFirmware\s*=\s*"([^"]+)"', r'"firmware"\s*:\s*"([^"]+)"')
        mac = grab(r'([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})')
        return DiscoveryResult(
            vendor=cls.vendor_id, model=model, firmware=firmware, mac=mac,
            confidence=1.0 if model else 0.6,
            raw={"model": model, "firmware": firmware, "mac": mac},
        )

    # --------------------------------------------------------- generate_config
    @classmethod
    def _linekey_type_id(cls, raw: Any) -> int:
        name = str(raw).strip().lower().replace(" ", "_")
        if name in _LINEKEY_TYPE_IDS:
            return _LINEKEY_TYPE_IDS[name]
        if name.isdigit():
            return int(name)
        raise ValueError(f"Yealink: tipo de linekey desconhecido: {raw!r}")

    @classmethod
    def _render_function_keys(
        cls, keys: list[dict[str, Any]], row: dict[str, Any],
    ) -> str:
        """function_keys do ambiente -> linhas linekey.<idx>.{type,value,label,line}.

        Mesma semantica dos outros adapters: `key`='LineKey2' -> indice 2;
        value vem da linha (value_source='linha' -> row[value_field]) ou fixo.
        """
        out: list[str] = []
        for fk in keys or []:
            key_name = str(fk.get("key", ""))
            m = re.search(r"(\d+)$", key_name)
            if not m:
                continue
            idx = int(m.group(1))
            type_id = cls._linekey_type_id(fk.get("type", "disabled"))
            if fk.get("value_source") == "linha":
                value = str(row.get(fk.get("value_field") or "numero_abreviado", "") or "")
            else:
                value = str(fk.get("value_fixed", "") or "")
            label = str(fk.get("label", "") or "")
            # Yealink: linekey.X.line é 1-based (1 -> account.1, 2 -> account.2).
            # Sem subtrair (o "-1" gerava índice negativo quando vinha 0/"0").
            try:
                acct = int(fk.get("account", 1) or 1)
            except (TypeError, ValueError):
                acct = 1
            line = acct if acct >= 1 else 1
            out.append(f"linekey.{idx}.type = {type_id}")
            out.append(f"linekey.{idx}.value = {value}")
            out.append(f"linekey.{idx}.label = {label}")
            out.append(f"linekey.{idx}.line = {line}")
        return "\n".join(out)

    @staticmethod
    def _render_web_admin(template: dict[str, Any]) -> str:
        """Troca a senha do admin web SOMENTE se o ambiente definiu `nova_web_password`.

        Yealink: `security.user_password = <usuario>:<senha>` (write-only; no
        export volta mascarado). O "usuario" e o papel fixo do firmware
        (admin/user/var) — `nova_web_user` vazio assume `admin`, igual aos outros
        adapters. Validado na bancada: trocar admin:<senha> autentica com a nova
        senha. Sem `nova_web_password` NAO mexe na credencial atual.
        """
        nova_pwd = str(template.get("nova_web_password", "") or "")
        if not nova_pwd:
            return ""
        nova_user = str(template.get("nova_web_user", "") or "").strip() or "admin"
        return f"## Troca da senha do admin web\nsecurity.user_password = {nova_user}:{nova_pwd}"

    def generate_config(self, template: dict[str, Any], row: dict[str, Any]) -> bytes:
        """Gera o .cfg (key=value) parcial — so conta SIP 1 + linekeys + fuso.

        Aplica a whitelist de prefixos como defesa em profundidade: qualquer
        chave fora de `_ALLOWED_PREFIXES` aborta a geracao (nunca tocar em rede).
        """
        active = 1 if row.get("account_active", 1) in (1, "1", True, "true", "on") else 0
        transport_raw = str(template.get("sip_transport", "udp")).lower()
        transport = _TRANSPORT_IDS.get(
            transport_raw, int(transport_raw) if transport_raw.isdigit() else 0,
        )
        tz_raw = str(template.get("timezone", "America/Sao_Paulo"))
        timezone = _TIMEZONE_OFFSETS.get(tz_raw, tz_raw if re.fullmatch(r"[+-]?\d+", tz_raw) else "-3")

        # Conta SIP alvo (1 ou 2). Config parcial: a outra conta fica intacta.
        sip_acct = "2" if str(template.get("sip_account", 1)) == "2" else "1"
        ctx = {
            "sip_acct": sip_acct,
            "account_active": str(active),
            "label": str(row.get("label") or row.get("conta_sip", "")),
            "display_name": str(row.get("display_name") or row.get("conta_sip", "")),
            "auth_id": str(row.get("auth_id") or row.get("conta_sip", "")),
            "user_id": str(row.get("conta_sip", "")),
            "password": str(row.get("senha_sip", "")),
            "sip_server": str(row.get("servidor_sip") or template.get("sip_server", "")),
            "sip_port": str(template.get("sip_port") or "5060"),
            "sip_transport": str(transport),
            "timezone": timezone,
            "function_keys_cfg": self._render_function_keys(
                template.get("function_keys", []) or [], row,
            ),
            "web_admin_cfg": self._render_web_admin(template),
        }
        cfg = _TEMPLATE_PATH.read_text(encoding="utf-8").format_map(ctx)
        self._assert_whitelist(cfg)
        return cfg.encode("utf-8")

    @staticmethod
    def _assert_whitelist(cfg: str) -> None:
        """Aborta se alguma linha 'chave = valor' usar prefixo fora da whitelist."""
        for line in cfg.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            if not any(key.startswith(p) for p in _ALLOWED_PREFIXES):
                raise RuntimeError(
                    f"Yealink: chave fora da whitelist (possivel rede!): {key!r}"
                )

    # ------------------------------------------------------------- send_config
    async def send_config(
        self, ip: str, creds: VendorCredentials, cfg: bytes, *, fmt: str = "cfg",
    ) -> None:
        """Import de config local: login -> token -> POST multipart -> checa result.

        O aparelho aplica as chaves presentes e pode reiniciar.
        """
        if fmt not in ("cfg", "xml"):  # 'xml' tolerado por compat de assinatura
            raise ValueError(f"Yealink: fmt deve ser 'cfg', recebido {fmt!r}")
        # Defesa em profundidade tambem no envio.
        self._assert_whitelist(cfg.decode("utf-8", "replace"))

        cli = await self._login(ip, creds)
        try:
            token, n_hex, e_hex = await self._get_token_and_rsa(ip, cli)
            # O servlet de upload EXIGE &maxlength=<RSAEncrypt("5MB")> (valor do
            # filedesp da pagina). Sem ele a resposta e {"result":"noparam"}.
            maxlen = self._rsa_encrypt_hex("5MB", n_hex, e_hex)
            files = {"UploadName": ("config.cfg", cfg, "application/octet-stream")}
            resp = await cli.post(
                f"https://{ip}{self._UPLOAD_PATH}&maxlength={maxlen}&token={token}",
                files=files,
                headers={"Referer": f"https://{ip}{self._TOKEN_PAGE}"},
                timeout=30.0,
            )
        finally:
            await cli.aclose()
        if resp.status_code in (401, 403):
            raise VendorAuthError(f"Yealink: upload recusado (HTTP {resp.status_code})")
        resp.raise_for_status()
        result = self._parse_upload_result(resp.text)
        # O firmware responde result NUMERICO (ex.: 5) em sucesso; o JS so trata
        # failed/false/large/noparam como falha. None = resposta inesperada.
        if result is None or result in ("failed", "false", "large", "noparam"):
            raise RuntimeError(f"Yealink: import retornou result={result!r}")

    @staticmethod
    def _parse_upload_result(html: str) -> str | None:
        """Extrai `result` do _RES_INFO_: string ("success"/"noparam"/...) ou numero."""
        m = re.search(r'"result"\s*:\s*"?([A-Za-z0-9]+)"?', html)
        return m.group(1).lower() if m else None

    # ------------------------------------------------------------ device actions
    # Homologado ao vivo (T31G, 2026-07-31): Action URI GET /servlet?key=<KEY>
    # com Basic Auth. NÃO usa a sessão web (401 sem Basic). Gate: lista de IP
    # confiável ("Action URI Allow IP List") — em produção o middleware está na
    # mesma sub-rede dos telefones. Volume é relativo (1 passo/req) → normalize
    # emite VOLUME_UP N vezes para garantir o teto.
    _VOLUME_STEPS = 10  # escala do Yealink vai a ~15; 10 presses garante o topo

    def capabilities(self) -> frozenset[str]:
        return frozenset({ACTION_NORMALIZE})

    async def execute_action(
        self, ip: str, creds: VendorCredentials, action: str, params: dict[str, Any],
    ) -> ActionResult:
        if action != ACTION_NORMALIZE:
            raise VendorActionUnsupported(f"yealink: ação {action!r} não suportada")
        auth = httpx.BasicAuth(creds.username, creds.password)
        async with self._client() as cli:
            # DND off + tira o mute (MUTE alterna; no idle volta ao normal)
            await self._action_uri(cli, ip, "DNDOff", auth)
            await self._action_uri(cli, ip, "MUTE", auth)
            for _ in range(self._VOLUME_STEPS):
                await self._action_uri(cli, ip, "VOLUME_UP", auth)
        return ActionResult(ok=True, detail="DND off + unmute + volume máximo")

    async def _action_uri(
        self, cli: httpx.AsyncClient, ip: str, key: str, auth: httpx.Auth,
    ) -> None:
        resp = await cli.get(f"https://{ip}/servlet?key={key}", auth=auth)
        if resp.status_code in (401, 403):
            raise VendorAuthError(
                f"Yealink: Action URI recusado ({resp.status_code}) — "
                "verifique credencial e a lista de IP confiável do aparelho"
            )
