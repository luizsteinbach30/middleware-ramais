"""Adapter Intelbras S3002 (linha S / firmware GoAhead-Webs).

ATENCAO: NAO confundir com `intelbras.py` (V-series / firmware RapidLogic) —
protocolo totalmente diferente. O S3002 e um firmware GoAhead OEM com paginas
`.asp` e endpoints `/goform/`, da mesma familia do FlyingVoice, mas com login e
rotas proprias.

Validado em lab 2026-06-03 contra 192.168.0.48 (S3002 fw V1.7.0.010412359):
  Server: GoAhead-Webs                    (fingerprint generico — confirmar titulo)
  GET / -> 302 /home.asp

Auth (plaintext, sessao por IP, login unico):
  POST /goform/SavewebLogin   username=<user>&password=<pwd>  (SEM hash; o
                              md5.js da pagina nao e usado no submit)
  -> sucesso: 302 Location /home.asp ; falha: 302 Location /login.asp
  Sessao e amarrada ao IP de origem (NAO ha cookie). Login concorrente e
  recusado enquanto a sessao anterior estiver ativa -> POST /goform/clearLogFlag
  limpa o flag de sessao antes de tentar de novo.

Discover (GET /home.asp autenticado):
  `<span id="Phone Model"></span></th><td>S3002</td>` (idem MAC Address,
  Software Version).

Envio de config (conta SIP 1):
  Pagina GET /name.asp (form id `Accountname`) -> replay + override da whitelist
  -> POST /goform/SaveSipUserCfg (HTTP/1.0 cru; hidden `Operate=Submit`).
  AccountID 0-based: 0 = conta 1 (homologada), 1 = conta 2 (NAO validada).

Teclas programaveis:
  Pagina GET /linekey.asp (form `WebDialRule`) -> POST /goform/SaveLineKeyCfg.
  Campos por tecla N: ptypeN / pSipAccountsN / pNAMEn / pNUMn.
  ptype confirmado no lang pack (SetSelectText, br.js): Linha=0, Discagem
  rapida=5, BLF=1, Broadsoft BLF=11. pSipAccounts: Conta 1=0, Conta 2=1,
  Qualquer=127.

Bloqueio de teclado/menu (paridade com o V-series):
  Pagina GET /SysConfig.asp (form `SysConfig`, aba "Lockkeys_child") -> POST
  /goform/SaveSysCfg. O firmware ESCOPA o save pela aba via hidden
  `currentPage=Lockkeys_child` — salvar o bloqueio NAO mexe nas abas Basic/Time.
  Campos: `LockKeys` (select), `PhoneLockTimeOut` (s, 0=so manual), `PhoneUnlockPIN`
  (4-15 digitos). `EmergencyCall` (numeros de emergencia) vive na mesma aba e
  volta intacto via replay. Capturado em lab 2026-06-04 contra 192.168.0.48.
  Opcoes do LockKeys confirmadas no lang pack br.js (SetSelectText):
    0=Desabilitar 1=Bloquear Menu 2=Bloquear Funcoes 3=Bloquear Tudo
    4=Bloquear Tudo e Atender Auto.
  Mapeamento do `keylock_enable` do ambiente (semantica V-series 0/1/2) para o
  S3002: 0->LockKeys 0 (off); 1 (Manual)->LockKeys 1 + timeout 0 (so manual);
  2 (Ativado)->LockKeys 1 + timeout = keylock_timeout (auto-lock). O S3002 tem um
  unico PIN (PhoneUnlockPIN) -> usa `keylock_password`; nao ha `menu_password`
  separado como no V-series.

Whitelist rigida (REGRA INVIOLAVEL — feedback-nunca-tocar-em-rede):
  SO sobrescrevemos as chaves SIP em ``_WHITELIST``. Tudo o mais (proxy, STUN,
  servidores secundarios, transporte, DNSSRV, etc.) volta com o valor atual do
  aparelho via replay. Config de rede L2/L3 vive em /NetWork.asp — NUNCA tocada.
  Validado por tests/unit/extension_configurator/test_intelbras_s3002.py.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar

import httpx

from . import _form_replay
from .base import DiscoveryResult, VendorAdapter, VendorAuthError, VendorCredentials

# Chaves do form SIP (/name.asp -> SaveSipUserCfg) que ESTE adapter pode
# sobrescrever. Qualquer coisa fora desta lista volta com o valor atual do
# aparelho (replay). NENHUMA chave de rede pode entrar aqui — garantido por teste.
_WHITELIST: frozenset[str] = frozenset({
    "AccountID",
    "EnableAccount",
    "UserName",      # Display Name
    "UserNumber",    # Sip Username (numero/usuario SIP)
    "appName",       # Authenticate Name (auth id)
    "Password",      # senha SIP (plaintext)
    "Describe",      # Label
    "DomainName",    # SIP Server
    "DomainNamePort",
})

# Tipos de tecla programavel (ptype) confirmados no lang pack do S3002.
_LINEKEY_TYPES: dict[str, int] = {
    "disabled": 0, "na": 0, "none": 0,
    "line": 0,
    "speed_dial": 5, "discagem_rapida": 5,
    "blf": 1,
    "blf_list": 11, "broadsoft_blf": 11,
}

# Campos do form SysConfig (/SysConfig.asp -> SaveSysCfg) que ESTE adapter pode
# sobrescrever ao gravar o bloqueio de teclado. `currentPage` escopa o save na
# aba do bloqueio (sem isso o firmware processaria a aba errada). NENHUM campo de
# rede entra aqui — e o form SysConfig (System: Basic/Time/Lockkeys) sequer
# contem config de rede (essa vive em /NetWork.asp, jamais tocada).
_SYSCFG_WHITELIST: frozenset[str] = frozenset({
    "LockKeys",          # 0=off 1=Bloquear Menu 2=Funcoes 3=Tudo 4=Tudo+AutoAtender
    "PhoneLockTimeOut",  # segundos ate auto-lock (0 = so manual)
    "PhoneUnlockPIN",    # PIN de desbloqueio (4-15 digitos)
    "currentPage",       # escopo do save (= "Lockkeys_child")
})


class IntelbrasS3002Adapter(VendorAdapter):
    vendor_id = "intelbras_s3002"

    _TIMEOUT = 8.0
    _LOGIN_MAX_TRIES = 4
    _LOGIN_RETRY_DELAY_S = 2.0

    _SIP_PAGE = "/name.asp"
    _SIP_SET = "/goform/SaveSipUserCfg"
    _SIP_FORM = "Accountname"  # form id
    _LINEKEY_PAGE = "/linekey.asp"
    _LINEKEY_SET = "/goform/SaveLineKeyCfg"
    _LINEKEY_FORM = "WebDialRule"
    _SYSCFG_PAGE = "/SysConfig.asp"
    _SYSCFG_SET = "/goform/SaveSysCfg"
    _SYSCFG_FORM = "SysConfig"
    _SYSCFG_CURRENT_PAGE = "Lockkeys_child"  # escopa o save na aba do bloqueio

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _client(timeout: float | None = None) -> httpx.AsyncClient:
        # GETs/login via httpx; o firmware responde HTTP/1.0 mas aceita request
        # 1.1 nessas rotas (validado com curl). O SAVE usa socket 1.0 cru.
        return httpx.AsyncClient(
            http1=True, http2=False,
            timeout=timeout or IntelbrasS3002Adapter._TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0", "Connection": "close"},
        )

    # ------------------------------------------------------------ fingerprint
    async def fingerprint(self, ip: str, creds: VendorCredentials | None = None) -> float:
        try:
            async with self._client(timeout=3.0) as cli:
                resp = await cli.get(f"http://{ip}/home.asp")
        except httpx.HTTPError:
            return 0.0
        server = resp.headers.get("Server", "").lower()
        body = resp.text
        if "goahead" not in server:
            return 0.0
        # Marcadores do firmware Intelbras linha S (home.asp serve mesmo deslogado).
        if "S3002" in body or "Intelbras Phone Setting" in body:
            return 1.0
        return 0.0

    # -------------------------------------------------------------------- auth
    async def _login(self, ip: str, creds: VendorCredentials) -> httpx.AsyncClient:
        """Loga (plaintext) e devolve o client. Sessao e por IP (sem cookie).

        Sucesso = 302 Location /home.asp. Falha de credencial = /login.asp.
        Como login concorrente tambem cai em /login.asp, tentamos clearLogFlag +
        retry antes de desistir como erro de credencial.
        """
        last_loc = ""
        for attempt in range(self._LOGIN_MAX_TRIES):
            cli = self._client()
            try:
                if attempt:
                    # limpa flag de sessao presa antes de re-tentar
                    try:
                        await cli.post(f"http://{ip}/goform/clearLogFlag")
                    except httpx.HTTPError:
                        pass
                resp = await cli.post(
                    f"http://{ip}/goform/SavewebLogin",
                    data={"username": creds.username, "password": creds.password},
                )
                last_loc = resp.headers.get("Location", "")
                if "home.asp" in last_loc or (resp.status_code == 200 and "login.asp" not in resp.text):
                    return cli
                await cli.aclose()
                await asyncio.sleep(self._LOGIN_RETRY_DELAY_S)
            except httpx.HTTPError:
                await cli.aclose()
                await asyncio.sleep(self._LOGIN_RETRY_DELAY_S)
        raise VendorAuthError(
            f"Intelbras S3002: login recusado (user/senha ou sessao presa) -> {last_loc!r}"
        )

    # --------------------------------------------------------------- discover
    _INFO_LABELS: ClassVar[dict[str, str]] = {
        "model": "Phone Model",
        "firmware": "Software Version",
        "mac": "MAC Address",
    }

    async def discover(self, ip: str, creds: VendorCredentials) -> DiscoveryResult:
        cli = await self._login(ip, creds)
        try:
            resp = await cli.get(f"http://{ip}/home.asp")
        finally:
            await cli.aclose()
        return self.parse_home_page(resp.text)

    @classmethod
    def parse_home_page(cls, html: str) -> DiscoveryResult:
        """Extrai modelo/firmware/MAC de /home.asp.

        Estrutura: `<span id="LABEL"></span> ... </th> <td>VALOR</td>`.
        """
        def find(label: str) -> str | None:
            pat = rf'id="{re.escape(label)}"[^>]*>.*?</th>\s*<td[^>]*>(.*?)</td>'
            m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
            if not m:
                return None
            return re.sub(r"\s+", " ", m.group(1).split("<", 1)[0]).strip() or None
        model = find(cls._INFO_LABELS["model"])
        firmware = find(cls._INFO_LABELS["firmware"])
        mac = find(cls._INFO_LABELS["mac"])
        if mac and not re.fullmatch(r"[0-9a-fA-F:]{17}", mac):
            mac = None
        return DiscoveryResult(
            vendor=cls.vendor_id, model=model, firmware=firmware, mac=mac,
            confidence=1.0 if model else 0.6,
            raw={"model": model, "firmware": firmware, "mac": mac},
        )

    # --------------------------------------------------------- generate_config
    def generate_config(self, template: dict[str, Any], row: dict[str, Any]) -> bytes:
        """Produz SOMENTE as chaves da whitelist (key=value por linha, ordenado).

        NAO e o corpo final do POST — o envio faz replay do form inteiro (ver
        send_config). Estes bytes sao a "intencao" de config e servem ao hash de
        status. Teclas programaveis saem como linhas LINEKEY<i>=ptype,acc,nome,num.
        """
        port = str(template.get("sip_port") or "5060")
        active = int(row.get("account_active", 1) or 0)
        # AccountID 0-based; conta 2 (=1) ainda nao validada -> default conta 1.
        account_id = "1" if str(template.get("sip_account", 1)) == "2" else "0"
        display = str(row.get("display_name") or row.get("label") or row.get("conta_sip", ""))
        # UserNumber ("Sip Username") e o usuario do REGISTER (sip:UserNumber@srv);
        # casa com RegisterUser/SipName do V-series -> usa auth_id (quando o PBX
        # tem auth name != numero do ramal). appName ("Authenticate Name") = auth_id
        # tambem; assim Sip Username == Authenticate Name, como o PBX exige.
        sip_user = str(row.get("auth_id") or row.get("conta_sip", ""))
        overrides = {
            "AccountID": account_id,
            "EnableAccount": "EnableAccount" if active else "",
            "UserName": display,
            "UserNumber": sip_user,
            "appName": sip_user,
            "Password": str(row.get("senha_sip", "")),
            "Describe": str(row.get("label") or display),
            "DomainName": str(row.get("servidor_sip") or template.get("sip_server", "")),
            "DomainNamePort": port,
        }
        bad = set(overrides) - _WHITELIST
        if bad:
            raise RuntimeError(f"Intelbras S3002: chaves fora da whitelist: {bad}")
        lines = [f"{k}={overrides[k]}" for k in sorted(overrides)]
        for idx, val in sorted(self._render_linekeys(template, row).items()):
            lines.append(f"LINEKEY{idx}={val}")
        # Troca de credencial web (admin do telefone). So quando o ambiente define
        # `nova_web_password` — senao NAO mexe na credencial atual. Aplicada via
        # /goform/SaveMaintenUsrCfg (pagina separada). Mesma semantica do V-series
        # e do FlyingVoice (`nova_web_user`/`nova_web_password`).
        nova_pass = str(template.get("nova_web_password", "") or "")
        if nova_pass:
            nova_user = str(template.get("nova_web_user", "") or "").strip() or "admin"
            lines.append(f"WEBADMIN_USER={nova_user}")
            lines.append(f"WEBADMIN_PASS={nova_pass}")
        # Bloqueio de teclado/menu (paridade com o V-series). So gerencia quando o
        # ambiente traz `keylock_enable` — o caller real (build_template) sempre
        # traz; chamadas minimas (testes de SIP puro) nao, e ai nao mexemos no
        # bloqueio. PIN validado no envio (4-15 digitos), nao aqui (o hash de
        # status precisa ser estavel mesmo com PIN ainda invalido).
        lines.extend(self._render_keylock(template))
        return ("\n".join(lines) + "\n").encode("utf-8")

    @staticmethod
    def _render_keylock(template: dict[str, Any]) -> list[str]:
        """`keylock_enable` (V-series 0/1/2) -> linhas de intencao LOCK* do S3002.

        0=off; 1 (Manual)=Bloquear Menu sem auto-lock (timeout 0); 2 (Ativado)=
        Bloquear Menu com auto-lock apos `keylock_timeout` s. Sem a chave no
        template, nao emite nada (nao gerencia o bloqueio).
        """
        if "keylock_enable" not in template:
            return []
        enable = int(template.get("keylock_enable", 0) or 0)
        lock_keys = 0 if enable == 0 else 1
        out = [f"LOCKKEYS={lock_keys}"]
        if lock_keys:
            timeout = 0 if enable == 1 else int(template.get("keylock_timeout", 30) or 0)
            out.append(f"LOCKTIMEOUT={timeout}")
            out.append(f"LOCKPIN={template.get('keylock_password', '') or ''}")
        return out

    @classmethod
    def _linekey_type_id(cls, raw: Any) -> int:
        name = str(raw).strip().lower().replace(" ", "_")
        if name in _LINEKEY_TYPES:
            return _LINEKEY_TYPES[name]
        if name.isdigit():
            return int(name)
        raise ValueError(f"Intelbras S3002: tipo de tecla desconhecido: {raw!r}")

    @classmethod
    def _render_linekeys(
        cls, template: dict[str, Any], row: dict[str, Any],
    ) -> dict[int, str]:
        """function_keys do ambiente -> {indice: 'ptype,pSipAccounts,pNAME,pNUM'}.

        pSipAccounts 0-based (Conta 1=0, Conta 2=1; 127=Qualquer). Discagem
        rapida/BLF usam o numero como pNUM; demais ficam so com o tipo.
        """
        out: dict[int, str] = {}
        for fk in template.get("function_keys", []) or []:
            m = re.search(r"(\d+)$", str(fk.get("key", "")))
            if not m:
                continue
            idx = int(m.group(1))
            ptype = cls._linekey_type_id(fk.get("type", "disabled"))
            acc_raw = int(fk.get("account", 1) or 1)
            psip = str(acc_raw - 1) if acc_raw >= 1 else "127"
            name = str(fk.get("label", "") or "")
            if fk.get("value_source") == "linha":
                num = str(row.get(fk.get("value_field", "numero_abreviado"), "") or "")
            else:
                num = str(fk.get("value_fixed", "") or "")
            out[idx] = f"{ptype},{psip},{name},{num}"
        return out

    @staticmethod
    def _parse_overrides(cfg: bytes) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in cfg.decode("utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
        return out

    # ------------------------------------------------------------- send_config
    async def send_config(
        self, ip: str, creds: VendorCredentials, cfg: bytes, *, fmt: str = "xml",
    ) -> None:
        """Aplica conta SIP 1 (e teclas) via replay do form + POST HTTP/1.0.

        O aparelho aplica ao vivo (reinicio so quando o firmware decidir). `fmt`
        e ignorado (compat com a assinatura do pipeline, que passa fmt='xml').
        """
        all_over = self._parse_overrides(cfg)
        lk_over = {
            int(k[len("LINEKEY"):]): v
            for k, v in all_over.items() if re.fullmatch(r"LINEKEY\d+", k)
        }
        web_user = all_over.get("WEBADMIN_USER")
        web_pass = all_over.get("WEBADMIN_PASS")
        lock_over = {k: v for k, v in all_over.items() if k.startswith("LOCK")}
        sip_over = {
            k: v for k, v in all_over.items()
            if not re.fullmatch(r"LINEKEY\d+", k)
            and not k.startswith("WEBADMIN_")
            and not k.startswith("LOCK")
        }
        bad = set(sip_over) - _WHITELIST
        if bad:
            raise RuntimeError(f"Intelbras S3002: recusando enviar chaves de rede/extra: {bad}")

        if sip_over:
            await self._post_form(
                ip, creds, self._SIP_PAGE, self._SIP_FORM, self._SIP_SET,
                sip_over, ensure={"Operate": "Submit"},
            )
        if lk_over:
            await self._post_form(
                ip, creds, self._LINEKEY_PAGE, self._LINEKEY_FORM, self._LINEKEY_SET,
                self._linekey_overrides(lk_over),
            )
        if lock_over:
            await self._apply_keylock(ip, creds, lock_over)
        # Credencial web do telefone (pagina /UpholdPassword.asp). PwdOld = senha
        # ATUAL (creds.password). Roda por ultimo: apos trocar, a sessao/credencial
        # muda e os proximos applies usam a nova credencial (chain do pipeline).
        if web_pass is not None:
            await self._send_web_admin(ip, creds, web_user or "admin", web_pass)

    async def _apply_keylock(
        self, ip: str, creds: VendorCredentials, lock_over: dict[str, str],
    ) -> None:
        """Grava o bloqueio de teclado/menu via replay de /SysConfig.asp.

        So sobrescreve LockKeys/PhoneLockTimeOut/PhoneUnlockPIN + currentPage; o
        resto da aba (inclusive EmergencyCall) volta com o valor atual do aparelho.
        Valida o PIN aqui (4-15 digitos) — sem PIN valido o firmware recusa o
        bloqueio, entao falhamos cedo com mensagem clara em vez de gravar quebrado.
        """
        mode = lock_over.get("LOCKKEYS", "0")
        overrides = {"LockKeys": mode, "currentPage": self._SYSCFG_CURRENT_PAGE}
        if mode != "0":
            pin = lock_over.get("LOCKPIN", "")
            if not (pin.isdigit() and 4 <= len(pin) <= 15):
                raise RuntimeError(
                    "Intelbras S3002: para ativar o bloqueio de teclado o PIN "
                    "(keylock_password) deve ter de 4 a 15 digitos."
                )
            overrides["PhoneLockTimeOut"] = lock_over.get("LOCKTIMEOUT", "0")
            overrides["PhoneUnlockPIN"] = pin
        bad = set(overrides) - _SYSCFG_WHITELIST
        if bad:
            raise RuntimeError(f"Intelbras S3002: chaves fora da whitelist de bloqueio: {bad}")
        await self._post_form(
            ip, creds, self._SYSCFG_PAGE, self._SYSCFG_FORM, self._SYSCFG_SET, overrides,
        )

    @staticmethod
    def _linekey_overrides(lk_over: dict[int, str]) -> dict[str, str]:
        """{idx: 'ptype,psip,name,num'} -> overrides ptype{n}/pSipAccounts{n}/pNAME{n}/pNUM{n}."""
        out: dict[str, str] = {}
        for idx, packed in lk_over.items():
            ptype, psip, name, num = [*packed.split(",", 3), "", "", "", ""][:4]
            out[f"ptype{idx}"] = ptype
            out[f"pSipAccounts{idx}"] = psip
            out[f"pNAME{idx}"] = name
            out[f"pNUM{idx}"] = num
        return out

    async def _post_form(
        self, ip: str, creds: VendorCredentials, page: str, form: str, action: str,
        overrides: dict[str, str], *, ensure: dict[str, str] | None = None,
    ) -> None:
        """login -> GET page -> replay form + overrides -> POST HTTP/1.0 -> 200/302."""
        cli = await self._login(ip, creds)
        try:
            html = (await cli.get(f"http://{ip}{page}")).text
        finally:
            await cli.aclose()
        pairs, _ = _form_replay.parse_form_fields(html, form)
        body = _form_replay.merge_body(pairs, overrides, ensure=ensure)
        status = _form_replay.http10_post(
            ip, action, body, headers={"Referer": f"http://{ip}{page}"},
        )
        if status not in (200, 302):
            raise RuntimeError(f"Intelbras S3002: {action} HTTP {status}")

    # ----------------------------------------------------- credencial web
    _WEB_PAGE = "/UpholdPassword.asp"
    _WEB_SET = "/goform/SaveMaintenUsrCfg"
    _WEB_ANCHOR = "PwdNew"  # o <form> nao tem name/id; ancoramos por este campo
    # So podemos sobrescrever role/usuario/senhas do admin. NTP/portas/SIP context
    # voltam via replay — NUNCA emitimos valor proprio para rede.
    _WEB_WHITELIST: frozenset[str] = frozenset(
        {"role", "UserName", "PwdOld", "PwdNew", "PwdConfirm"}
    )

    async def _send_web_admin(
        self, ip: str, creds: VendorCredentials, user: str, password: str,
    ) -> None:
        """Troca usuario/senha do admin web via SaveMaintenUsrCfg (replay + HTTP/1.0).

        PwdOld = senha atual (creds.password). A pagina tem DOIS grupos de radio
        `role` (admin/user) ambos `checked`; forcamos um unico `role=admin` para
        nao gravar a conta `user` por engano. Senha nova exige >= 8 chars (regra do
        firmware). Senha vai plaintext (o JS so valida, nao codifica).
        """
        cli = await self._login(ip, creds)
        try:
            html = (await cli.get(f"http://{ip}{self._WEB_PAGE}")).text
        finally:
            await cli.aclose()
        pairs, _ = _form_replay.parse_form_fields(html, self._WEB_ANCHOR)
        # remove os radios `role` duplicados do replay; reescrevemos um so.
        base = [(n, v) for n, v in pairs if n != "role"]
        over = {
            "UserName": user,
            "PwdOld": creds.password,
            "PwdNew": password,
            "PwdConfirm": password,
        }
        assert set(over) | {"role"} <= self._WEB_WHITELIST  # defesa
        body = _form_replay.merge_body(
            [("role", "admin"), *base], over,
        )
        status = _form_replay.http10_post(
            ip, self._WEB_SET, body, headers={"Referer": f"http://{ip}{self._WEB_PAGE}"},
        )
        if status not in (200, 302):
            raise RuntimeError(f"Intelbras S3002: SaveMaintenUsrCfg HTTP {status}")
