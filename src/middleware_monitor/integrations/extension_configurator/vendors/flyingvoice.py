"""Adapter FlyingVoice (P-series, ex.: P10).

Validado em lab/produção 2026-05-23 contra IP 10.60.239.221 (P10, firmware
V0.11.6) com o ramal assai-8125 / servidor 10.173.1.50 -> status `Registered`,
SEM reboot e com a rede do aparelho intacta (diff de /internet/mwan.asp
antes x depois == vazio).

Fingerprint:
  GET /  -> 302 /index.asp  (sem header Server). A pagina de login tem o form
  `websLogin` que posta em /goform/websLogin + campo hidden CheckString.

Auth:
  1. GET  /index.asp                 -> extrai CheckString (token na pagina)
  2. POST /goform/websLogin          user_name, password (plaintext),
                                      CheckString, forcelogout=1
     -> 302 + Set-Cookie ASPSSIONID. Login e UNICO (forcelogout derruba a
     sessao concorrente). Sem captcha. Logout: /goform/websLogout.

Envio de config (conta SIP 1):
  Pagina /voip/SIP_Account1.asp, form `sip_account_1` -> POST /goform/setSip_account.

  *** DUAS PEGADINHAS CRITICAS (validadas) ***
  (1) EXIGE HTTP/1.0. Em HTTP/1.1 (httpx/curl default) o firmware fecha a
      conexao sem responder e NADA e salvo. Com `POST ... HTTP/1.0` +
      Connection: close + Content-Length (socket cru) retorna 302 e salva.
      Mesma classe do bug HTTP/1.0 do Intelbras (RapidLogic).
  (2) REPLAY DO FORM INTEIRO. POST so com a whitelist e rejeitado; o cgi exige
      reenviar TODOS os ~206 campos do form. Estrategia: GET a pagina, parseia
      todos inputs/selects com os valores ATUAIS do aparelho, e sobrescreve
      SOMENTE a whitelist SIP. Campos nao-whitelist voltam com o valor que ja
      estava no aparelho (idempotente) -> rede/preferencias preservadas.

Whitelist rigida (REGRA INVIOLAVEL — feedback-nunca-tocar-em-rede):
  SO sobrescrevemos as chaves em ``_WHITELIST``. NUNCA emitir valores nossos
  para chaves de rede — a pagina da conta ainda traz ``DBID_DNSSRV_DOMAIN``
  (DNS por conta), e ha ``mwan_*/lan_*/dhcp*/ipsec_*/VPNSRV_*/Wsc*/
  DBID_PROFILE_RULE/PRV_*/DBID_TR_ACS_*/PrvUser*`` no aparelho. A senha SIP vai
  PLAINTEXT (o onSubmit so valida, nao codifica). Validado por
  tests/unit/extension_configurator/test_flyingvoice.py.

Status de homologacao (P10 fw V0.11.6, validado em produção 2026-05-23):
  - Registro SIP (conta 1): VALIDADO ao vivo (ramal Registered, rede intacta).
  - Softkeys (todas): VALIDADO ao vivo (DND<->MENU, preservando as demais).
  - Troca de credencial web (`nova_web_*` -> /goform/setSysAdm): IMPLEMENTADO
    porem PENDENTE de validacao em hardware. Em teste o setSysAdm nao respondeu
    (timeout) e a credencial NAO mudou (admin/admin permaneceu) — falta acertar
    o que o cgi exige. So roda quando o ambiente define `nova_web_password`; o
    provisionamento SIP normal nao o aciona. NAO testar em telefone de producao.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from . import _form_replay
from .base import DiscoveryResult, VendorAdapter, VendorAuthError, VendorCredentials

# Chaves que ESTE adapter pode sobrescrever no form da conta SIP. Qualquer
# coisa fora desta lista volta com o valor atual do aparelho (replay). NENHUMA
# chave de rede pode entrar aqui. O teste de whitelist garante isso.
_WHITELIST: frozenset[str] = frozenset({
    "DBID_SIP_ENABLE",
    "DBID_ALTER_SIP_SERVER_HOSTNAME",
    "DBID_ALTER_SIP_SERVER_PORT",
    "DBID_SIP_PHONE_NUM",
    "DBID_SIP_ACCOUNT",
    "DBID_SIP_PASSWORD",
    "DBID_SIP_DIS_NAME",
})

# Funções de softkey suportadas pelo P10 (número usado em DBID_SOFTKEYn).
# Extraído do <select> de /phone/Phone_MultiFunc.asp + lang pack (2026-05-23).
# Nenhuma destas mexe em rede — softkey e funcao de UI/telefonia.
_SOFTKEY_FUNCS: dict[str, int] = {
    "disabled": 0, "na": 0, "none": 0,
    "history": 1, "historico": 1,
    "local_group": 2,
    "directory": 3, "diretorio": 3,
    "ldap": 4,
    "menu": 5,
    "dnd": 6,
    "speed_dial": 8, "discagem_rapida": 8,
    "status": 9,
    "account_up": 10,
    "xml_browser": 12,
    "paging": 15, "paging_list": 16,
    "account_down": 27,
    "corp_directory": 28,
    "usb_record": 33,
    "xml_group": 34,
}
# SoftKey1..4 (exkey_1..4) sao as 4 teclas de tela do P10. O form
# /goform/saveMultiFunc na verdade tem 24 slots (softkeys + navegacao); so
# sobrescrevemos os indices pedidos e preservamos os demais (replay).
_SOFTKEY_COUNT = 4
_SOFTKEY_SLOTS = 24


class FlyingVoiceAdapter(VendorAdapter):
    vendor_id = "flyingvoice"

    _TIMEOUT = 8.0
    _ACCOUNT_PAGE = "/voip/SIP_Account1.asp"
    _SET_PATH = "/goform/setSip_account"
    _FORM_NAME = "sip_account_1"

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _client(timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout or FlyingVoiceAdapter._TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0", "Connection": "close"},
        )

    _checkstring = staticmethod(_form_replay.checkstring)

    # ------------------------------------------------------------ fingerprint
    async def fingerprint(self, ip: str) -> float:
        try:
            async with self._client(timeout=3.0) as cli:
                resp = await cli.get(f"http://{ip}/index.asp")
        except httpx.HTTPError:
            return 0.0
        body = resp.text
        if "/goform/websLogin" in body and "CheckString" in body:
            return 1.0
        return 0.0

    # -------------------------------------------------------------------- auth
    async def _login(
        self, ip: str, creds: VendorCredentials,
    ) -> tuple[httpx.AsyncClient, str]:
        """Loga e devolve (client autenticado, cookie ASPSSIONID)."""
        cli = self._client()
        try:
            resp = await cli.get(f"http://{ip}/index.asp")
            cs = self._checkstring(resp.text)
            if not cs:
                raise RuntimeError("FlyingVoice: CheckString nao encontrado no login")
            await cli.post(
                f"http://{ip}/goform/websLogin",
                data={
                    "user_name": creds.username,
                    "password": creds.password,
                    "CheckString": cs,
                    "forcelogout": "1",
                },
            )
            cookie = cli.cookies.get("ASPSSIONID")
            if not cookie:
                await cli.aclose()
                raise VendorAuthError("FlyingVoice: login recusado (sem ASPSSIONID)")
            return cli, cookie
        except httpx.HTTPError as exc:
            await cli.aclose()
            raise RuntimeError(f"FlyingVoice: falha de rede no login: {exc}") from exc

    # --------------------------------------------------------------- discover
    async def discover(self, ip: str, creds: VendorCredentials) -> DiscoveryResult:
        cli, _ = await self._login(ip, creds)
        try:
            resp = await cli.get(f"http://{ip}/status/Status_Basic.asp")
        finally:
            await cli.aclose()
        html = resp.text
        fw = None
        m = re.search(r"firmware_v[^V]*(V[0-9.]+)", html)
        if m:
            fw = m.group(1)
        return DiscoveryResult(
            vendor=self.vendor_id, model=None, firmware=fw,
            confidence=1.0,
        )

    # --------------------------------------------------------- generate_config
    def generate_config(self, template: dict[str, Any], row: dict[str, Any]) -> bytes:
        """Produz SOMENTE as chaves da whitelist (key=value por linha, ordenado).

        Isto NAO e o corpo final do POST — o envio faz replay do form inteiro
        (ver send_config). Estes bytes sao a "intencao" de config: o que vamos
        sobrescrever. Servem tambem para o hash de status (compute_line_hash).
        """
        port = str(template.get("sip_port") or "5060")
        active = "1" if int(row.get("account_active", 1) or 0) else "0"
        overrides = {
            "DBID_SIP_ENABLE": active,
            "DBID_ALTER_SIP_SERVER_HOSTNAME": str(
                row.get("servidor_sip") or template.get("sip_server", "")
            ),
            "DBID_ALTER_SIP_SERVER_PORT": port,
            "DBID_SIP_PHONE_NUM": str(row.get("conta_sip", "")),
            "DBID_SIP_ACCOUNT": str(row.get("auth_id") or row.get("conta_sip", "")),
            "DBID_SIP_PASSWORD": str(row.get("senha_sip", "")),
            "DBID_SIP_DIS_NAME": str(row.get("display_name") or row.get("label", "")),
        }
        # Defesa em profundidade: nunca deixar escapar nada fora da whitelist.
        bad = set(overrides) - _WHITELIST
        if bad:
            raise RuntimeError(f"FlyingVoice: chaves fora da whitelist: {bad}")
        lines = [f"{k}={overrides[k]}" for k in sorted(overrides)]
        # Softkeys (DSS de tela). Sao do ambiente (template.function_keys), nao
        # da linha. Vao como linhas SOFTKEY<i>=type,line,value,label,ext e o
        # send_config aplica via /goform/saveMultiFunc (pagina separada).
        for idx, val in sorted(self._render_softkeys(template, row).items()):
            lines.append(f"SOFTKEY{idx}={val}")
        # Troca de credencial web (admin do telefone). So quando o ambiente
        # define `nova_web_password` — senao NAO mexe na credencial atual.
        # Aplicado via /goform/setSysAdm (pagina separada). Mesma semantica do
        # Intelbras (`nova_web_user`/`nova_web_password`).
        nova_pass = str(template.get("nova_web_password", "") or "")
        if nova_pass:
            nova_user = str(template.get("nova_web_user", "") or "").strip() or "admin"
            lines.append(f"WEBADMIN_USER={nova_user}")
            lines.append(f"WEBADMIN_PASS={nova_pass}")
        return ("\n".join(lines) + "\n").encode("utf-8")

    # ----------------------------------------------------------- softkeys
    @classmethod
    def _softkey_type_id(cls, raw: Any) -> int:
        name = str(raw).strip().lower().replace(" ", "_")
        if name in _SOFTKEY_FUNCS:
            return _SOFTKEY_FUNCS[name]
        if name.isdigit():
            return int(name)
        raise ValueError(f"FlyingVoice: tipo de softkey desconhecido: {raw!r}")

    @classmethod
    def _render_softkeys(
        cls, template: dict[str, Any], row: dict[str, Any],
    ) -> dict[int, str]:
        """function_keys do ambiente -> {indice_softkey: 'type,line,value,label,ext'}.

        So indices 1.._SOFTKEY_COUNT sao aceitos. Speed Dial (8) usa
        account/value/label; demais funcoes ficam 'type,,,,'.
        """
        out: dict[int, str] = {}
        for fk in template.get("function_keys", []) or []:
            key = str(fk.get("key", ""))
            m = re.search(r"(\d+)$", key)
            if not m:
                continue
            idx = int(m.group(1))
            if not (1 <= idx <= _SOFTKEY_COUNT):
                continue
            t = cls._softkey_type_id(fk.get("type", "disabled"))
            line = value = ext = ""
            label = str(fk.get("label", "") or "")
            if t == 8:  # Discagem Rapida
                # A UI expoe "Account" 1-based (1 = Conta 1). O P10 indexa a
                # conta da softkey 0-based no campo `line` (0 = Conta 1, ver
                # fixture DBIDArray do hardware). Sem o -1 a tecla cai na conta
                # SEGUINTE (Conta 1 da tela virava Conta 2 no telefone). Clamp
                # >=0 tolera 0/vazio (tratado como Conta 1).
                acct = int(fk.get("account", 1) or 1)
                line = str(max(acct - 1, 0))
                if fk.get("value_source") == "linha":
                    value = str(row.get(fk.get("value_field", "numero_abreviado"), "") or "")
                else:
                    value = str(fk.get("value_fixed", "") or "")
                ext = str(fk.get("value2", "") or "")
            out[idx] = f"{t},{line},{value},{label},{ext}"
        return out

    @staticmethod
    def _parse_overrides(cfg: bytes) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in cfg.decode("utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
        return out

    # ------------------------------------------------------------- form replay
    _parse_form_fields = staticmethod(_form_replay.parse_form_fields)

    @staticmethod
    def _http10_post(ip: str, path: str, body: str, cookie: str, referer: str) -> int:
        """POST cru em HTTP/1.0 com a sessao do FlyingVoice (cookie ASPSSIONID)."""
        return _form_replay.http10_post(
            ip, path, body,
            headers={"Cookie": f"ASPSSIONID={cookie}", "Referer": referer},
        )

    async def send_config(
        self, ip: str, creds: VendorCredentials, cfg: bytes, *, fmt: str = "cfg",
    ) -> None:
        """Aplica a conta SIP 1: login -> GET form -> merge whitelist -> POST 1.0.

        O aparelho aplica ao vivo (sem reboot) e retorna 302.
        """
        from urllib.parse import quote

        all_over = self._parse_overrides(cfg)
        sk_over = {
            int(k[len("SOFTKEY"):]): v
            for k, v in all_over.items() if re.fullmatch(r"SOFTKEY\d+", k)
        }
        web_user = all_over.get("WEBADMIN_USER")
        web_pass = all_over.get("WEBADMIN_PASS")
        sip_over = {
            k: v for k, v in all_over.items()
            if not re.fullmatch(r"SOFTKEY\d+", k) and not k.startswith("WEBADMIN_")
        }
        bad = set(sip_over) - _WHITELIST
        if bad:
            raise RuntimeError(f"FlyingVoice: recusando enviar chaves de rede/extra: {bad}")

        # --- conta SIP (pagina /voip/SIP_Account1.asp) ---
        if sip_over:
            cli, cookie = await self._login(ip, creds)
            try:
                html = (await cli.get(f"http://{ip}{self._ACCOUNT_PAGE}")).text
            finally:
                await cli.aclose()
            pairs, cs = self._parse_form_fields(html, self._FORM_NAME)
            body = self._merge_body(pairs, cs, sip_over, self._FORM_NAME, quote)
            status = self._http10_post(
                ip, self._SET_PATH, body, cookie,
                referer=f"http://{ip}{self._ACCOUNT_PAGE}",
            )
            if status not in (200, 302):
                raise RuntimeError(f"FlyingVoice: setSip_account HTTP {status}")

        # --- softkeys (pagina /phone/Phone_MultiFunc.asp) ---
        if sk_over:
            await self._send_softkeys(ip, creds, sk_over, quote)

        # --- credencial web do telefone (pagina /adm/management.asp) ---
        if web_pass is not None:
            await self._send_web_admin(ip, creds, web_user or "admin", web_pass, quote)

    @staticmethod
    def _merge_body(pairs, cs, overrides, form_name, quote):
        """Replay do form: sobrescreve overrides, refresca CheckString, garante FormName."""
        return _form_replay.merge_body(
            pairs, overrides, cs=cs, ensure={"FormName": form_name},
        )

    _MULTIFUNC_PAGE = "/phone/Phone_MultiFunc.asp"
    _MULTIFUNC_SET = "/goform/saveMultiFunc"
    _MULTIFUNC_FORM = "multi_functional_key"

    async def _send_softkeys(
        self, ip: str, creds: VendorCredentials, sk_over: dict[int, str], quote,
    ) -> None:
        """Aplica softkeys via saveMultiFunc, preservando as nao-listadas.

        Le o estado atual de TODAS as 24 teclas (vars JS DBIDArray1..24 na
        pagina, server-rendered), sobrescreve so os indices pedidos, e posta
        exkey_1..24 = 'type,line,value,label,ext' (HTTP/1.0).
        """
        cli, cookie = await self._login(ip, creds)
        try:
            html = (await cli.get(f"http://{ip}{self._MULTIFUNC_PAGE}")).text
        finally:
            await cli.aclose()

        current = self._parse_dbid_arrays(html)  # {1..24: 'type,line,value,label,ext'}
        pairs, cs = self._parse_form_fields(html, self._MULTIFUNC_FORM)
        # remove os exkey_* vazios do replay (vamos reescrever todos)
        base = [(n, v) for n, v in pairs if not re.fullmatch(r"exkey_\d+", n)]
        body_pairs: list[tuple[str, str]] = []
        for n, v in base:
            body_pairs.append((n, cs if n == "CheckString" else v))
        if not any(n == "FormName" for n, _ in body_pairs):
            body_pairs.append(("FormName", self._MULTIFUNC_FORM))
        if not any(n == "MakeSure" for n, _ in body_pairs):
            body_pairs.append(("MakeSure", "Save"))
        for i in range(1, _SOFTKEY_SLOTS + 1):
            val = sk_over.get(i, current.get(i, ",,,,"))
            body_pairs.append((f"exkey_{i}", val))

        body = "&".join(f"{quote(n, safe='')}={quote(v, safe='')}" for n, v in body_pairs)
        status = self._http10_post(
            ip, self._MULTIFUNC_SET, body, cookie,
            referer=f"http://{ip}{self._MULTIFUNC_PAGE}",
        )
        if status not in (200, 302):
            raise RuntimeError(f"FlyingVoice: saveMultiFunc HTTP {status}")

    @staticmethod
    def _parse_dbid_arrays(html: str) -> dict[int, str]:
        """Le DBIDArray1..24 = "type,line,value,label,ext" (estado atual)."""
        out: dict[int, str] = {}
        for m in re.finditer(r'DBIDArray(\d+)\s*=\s*"([^"]*)"', html):
            out[int(m.group(1))] = m.group(2)
        return out

    # ----------------------------------------------------- credencial web
    _WEB_PAGE = "/adm/management.asp"
    _WEB_SET = "/goform/setSysAdm"
    _WEB_FORM = "Adm"
    # No form `Adm` so podemos sobrescrever usuario/senha do admin web. O resto
    # (NTP, DST, DBID_WEB_PORT/SSL_PORT, QoS...) volta com o valor atual via
    # replay — NUNCA emitimos valor proprio para porta/rede.
    _WEB_WHITELIST: frozenset[str] = frozenset({"admuser", "admpass", "confirm_password"})

    async def _send_web_admin(
        self, ip: str, creds: VendorCredentials, user: str, password: str, quote,
    ) -> None:
        """Troca usuario/senha do admin web via setSysAdm (replay + HTTP/1.0).

        Senha vai PLAINTEXT (AdmFormCheck so valida, nao codifica). Sobrescreve
        apenas admuser/admpass/confirm_password; portas/NTP preservados.
        """
        cli, cookie = await self._login(ip, creds)
        try:
            html = (await cli.get(f"http://{ip}{self._WEB_PAGE}")).text
        finally:
            await cli.aclose()
        pairs, cs = self._parse_form_fields(html, self._WEB_FORM)
        over = {"admuser": user, "admpass": password, "confirm_password": password}
        # Defesa: o que sobrescrevemos no form web tem que ser so credencial.
        assert set(over) <= self._WEB_WHITELIST
        body = self._merge_body(pairs, cs, over, self._WEB_FORM, quote)
        status = self._http10_post(
            ip, self._WEB_SET, body, cookie, referer=f"http://{ip}{self._WEB_PAGE}",
        )
        if status not in (200, 302):
            raise RuntimeError(f"FlyingVoice: setSysAdm HTTP {status}")
