"""UniFi Protect — a API do console (UDM, UNVR, Cloud Key) (``cftv.unifi``).

Escrito pela documentação pública/comunitária da API do console. **Não
homologado** até a prova num console real (ADR 0037, decisão 3). A credencial
é do tipo ``API``: um usuário local do console, **só de leitura**.

O que é pedido:

1. ``POST /api/auth/login`` com ``{username, password}`` — **autenticação**, a única
   escrita permitida (não muda configuração). O console devolve o cookie ``TOKEN``
   e o token CSRF no cabeçalho ``X-CSRF-Token``.
2. ``GET /proxy/protect/api/bootstrap`` — com o cookie e o ``X-CSRF-Token``. Traz
   ``cameras[]`` (``isConnected``, ``state``, ``isRecording``) e ``nvr`` (discos em
   ``systemInfo.storage``).

Decisões que não se re-derivam lendo o código:

- **A sessão é reaproveitada** (em memória, por endereço + usuário + impressão da
  senha): o console limita tentativas de login, e um login a cada minuto por
  gravador acabaria bloqueando o usuário. ``401`` no bootstrap → um login novo, uma vez.
- **A hora do console é o cabeçalho ``Date`` da resposta do bootstrap** — é o relógio
  do próprio console no momento da resposta; o ``bootstrap`` não tem um campo "hora
  agora" estável entre versões.
- **O número do canal é a posição da câmera ordenada pelo ``id``**, que não muda
  quando a câmera é renomeada.
"""

from __future__ import annotations

import hashlib
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .base import (
    DISCO_DEFEITO,
    DISCO_OK,
    SEM_VIDEO,
    TIMEOUT_S,
    VIDEO_DESCONHECIDO,
    VIDEO_OK,
    Alvo,
    Canal,
    CftvAdapter,
    Disco,
    ErroDeLeitura,
    LeituraCftv,
    RespostaInvalida,
    SenhaRecusada,
    cliente_http,
    gb,
    local_para_utc,
)

LOGIN = "/api/auth/login"
BOOTSTRAP = "/proxy/protect/api/bootstrap"

# (base, usuário, impressão da senha) → (cookies, token CSRF). Só em memória.
_SESSOES: dict[tuple[str, str, str], tuple[httpx.Cookies, str | None]] = {}


def esquecer_sessoes() -> None:
    _SESSOES.clear()


def _chave(alvo: Alvo) -> tuple[str, str, str]:
    return alvo.base, alvo.usuario, hashlib.sha256(alvo.senha.encode("utf-8")).hexdigest()[:12]


def _csrf(resposta: httpx.Response) -> str | None:
    valor = resposta.headers.get("x-updated-csrf-token") or resposta.headers.get("x-csrf-token")
    return str(valor) if valor else None


async def _pedir(cli: httpx.AsyncClient, metodo: str, url: str, caminho: str, **kw: Any) -> httpx.Response:
    try:
        return await cli.request(metodo, url, **kw)
    except httpx.TimeoutException as exc:
        raise ErroDeLeitura(f"o console não respondeu a tempo em {caminho}") from exc
    except httpx.ConnectError as exc:
        raise ErroDeLeitura(f"não conectou ao console: {str(exc).strip() or 'conexão recusada'}") from exc
    except httpx.HTTPError as exc:
        raise ErroDeLeitura(f"{type(exc).__name__} em {caminho}") from exc


async def _entrar(cli: httpx.AsyncClient, alvo: Alvo) -> tuple[httpx.Cookies, str | None]:
    cli.cookies = httpx.Cookies()  # sessão velha não se mistura com a nova
    r = await _pedir(
        cli,
        "POST",
        alvo.base + LOGIN,
        LOGIN,
        json={"username": alvo.usuario, "password": alvo.senha, "rememberMe": False},
    )
    if r.status_code in (400, 401, 403):
        raise SenhaRecusada()
    if r.status_code == 429:
        raise ErroDeLeitura("o console recusou por excesso de tentativas de login (HTTP 429)")
    if r.status_code != 200:
        raise ErroDeLeitura(f"HTTP {r.status_code} em {LOGIN}")
    if not r.cookies.get("TOKEN"):
        raise RespostaInvalida(f"{LOGIN}: o console aceitou e não devolveu o cookie TOKEN")
    cookies = httpx.Cookies()
    for nome, valor in r.cookies.items():
        cookies.set(nome, valor)
    return cookies, _csrf(r)


def _discos(nvr: dict[str, Any]) -> list[Disco]:
    armazenamento = (nvr.get("systemInfo") or {}).get("storage") or {}
    if not isinstance(armazenamento, dict):
        return []
    dispositivos = armazenamento.get("devices")
    livre_total = armazenamento.get("available")
    if isinstance(dispositivos, list) and dispositivos:
        saida = []
        for i, d in enumerate(dispositivos, start=1):
            if not isinstance(d, dict):
                continue
            saude = str(d.get("healthy", "")).lower()
            saida.append(
                Disco(
                    numero=i,
                    estado=DISCO_OK if saude in ("good", "true") else DISCO_DEFEITO,
                    total_gb=gb(d.get("size")) if isinstance(d.get("size"), int | float) else None,
                    livre_gb=gb(livre_total)
                    if len(dispositivos) == 1 and isinstance(livre_total, int | float)
                    else None,
                )
            )
        return saida
    tamanho = armazenamento.get("size")
    if isinstance(tamanho, int | float):
        livre = gb(livre_total) if isinstance(livre_total, int | float) else None
        return [Disco(numero=1, estado=DISCO_OK, total_gb=gb(tamanho), livre_gb=livre)]
    return []


def _canais(cameras: list[Any]) -> list[Canal]:
    adotadas = [c for c in cameras if isinstance(c, dict) and c.get("isAdopted", True) is not False]
    saida = []
    for numero, cam in enumerate(sorted(adotadas, key=lambda c: str(c.get("id", ""))), start=1):
        conectada = cam.get("isConnected")
        if not isinstance(conectada, bool) and isinstance(cam.get("state"), str):
            conectada = cam["state"].upper() == "CONNECTED"
        gravando = cam.get("isRecording")
        saida.append(
            Canal(
                numero=numero,
                nome=str(cam["name"])[:128] if cam.get("name") else None,
                video=VIDEO_OK
                if conectada is True
                else SEM_VIDEO
                if conectada is False
                else VIDEO_DESCONHECIDO,
                gravando=gravando if isinstance(gravando, bool) else None,
            )
        )
    return saida


class UnifiAdapter(CftvAdapter):
    adaptador_id = "unifi"
    modulo = "cftv.unifi"
    tipo_de_credencial = "API"

    async def ler(self, alvo: Alvo, *, timeout_s: float = TIMEOUT_S) -> LeituraCftv:
        chave = _chave(alvo)
        sessao = _SESSOES.get(chave)
        async with cliente_http(timeout_s) as cli:
            for tentativa in range(2):
                if sessao is None:
                    sessao = await _entrar(cli, alvo)
                    _SESSOES[chave] = sessao
                cookies, csrf = sessao
                cabecalhos = {"Accept": "application/json"}
                if csrf:
                    cabecalhos["X-CSRF-Token"] = csrf
                cli.cookies = cookies
                r = await _pedir(cli, "GET", alvo.base + BOOTSTRAP, BOOTSTRAP, headers=cabecalhos)
                if r.status_code in (401, 403) and tentativa == 0:
                    _SESSOES.pop(chave, None)
                    sessao = None
                    continue
                break
        if r.status_code in (401, 403):
            _SESSOES.pop(chave, None)
            raise SenhaRecusada("o console recusou a sessão logo depois do login")
        if r.status_code != 200:
            raise ErroDeLeitura(f"HTTP {r.status_code} em {BOOTSTRAP}")
        if not r.content.strip():
            raise RespostaInvalida(f"HTTP 200 com a resposta vazia em {BOOTSTRAP}")
        try:
            corpo = r.json()
        except ValueError as exc:
            raise RespostaInvalida(f"{BOOTSTRAP}: a resposta não é JSON") from exc
        if not isinstance(corpo, dict) or not isinstance(corpo.get("nvr"), dict):
            raise RespostaInvalida(f"{BOOTSTRAP}: sem o objeto nvr")
        if not isinstance(corpo.get("cameras"), list):
            raise RespostaInvalida(f"{BOOTSTRAP}: sem a lista cameras")
        nvr = corpo["nvr"]

        hora = None
        if data := r.headers.get("date"):
            try:
                hora = local_para_utc(parsedate_to_datetime(data))
            except (TypeError, ValueError):
                hora = None
        return LeituraCftv(
            canais=_canais(corpo["cameras"]),
            discos=_discos(nvr),
            hora_do_gravador=hora,
            modelo=str(nvr.get("hardwarePlatform") or nvr.get("type") or "") or None,
            firmware=str(nvr.get("version") or "") or None,
        )
