"""Helpers de *replay de formulario* para firmwares GoAhead/`.asp` + `/goform/`.

Compartilhado entre os adapters FlyingVoice (P-series) e Intelbras S3002, que
seguem o mesmo padrao: a config so e aceita se o POST reenviar TODOS os campos
do form (a whitelist sozinha e rejeitada), e o firmware so fala HTTP/1.0 direito.

Estrategia: GET a pagina -> parseia todos os inputs/selects com os valores
ATUAIS do aparelho -> sobrescreve SOMENTE a whitelist -> POST cru em HTTP/1.0.
Campos nao-whitelist voltam com o valor que ja estava no aparelho (idempotente),
preservando rede/preferencias. Validado em hardware (P10 2026-05-23; S3002
2026-06-03).
"""

from __future__ import annotations

import re
import socket
from urllib.parse import quote


def checkstring(html: str) -> str | None:
    """Extrai o token CSRF `CheckString` (FlyingVoice). S3002 nao usa -> None."""
    m = re.search(r'name="CheckString"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


def parse_form_fields(html: str, form_name: str) -> tuple[list[tuple[str, str]], str]:
    """Extrai (todos os pares name=value do form, CheckString).

    Replica o que o browser enviaria: inputs text/hidden/password com seu value;
    checkbox/radio so quando `checked`; select com a option `selected`
    (fallback: primeira). `form_name` casa tanto com `name="..."` quanto
    `id="..."` na tag <form> (o S3002 identifica o form SIP por `id`).
    """
    i = html.find(f'name="{form_name}"')
    if i < 0:
        i = html.find(f'id="{form_name}"')
    if i < 0:
        raise RuntimeError(f"form {form_name} nao encontrado")
    start = html.rfind("<form", 0, i)
    end = html.find("</form>", start)
    region = html[start:end]
    pairs: list[tuple[str, str]] = []
    for m in re.finditer(r"<input\b[^>]*>", region, re.I):
        tag = m.group(0)
        nm = re.search(r'name="([^"]+)"', tag)
        if not nm:
            continue
        name = nm.group(1)
        typ_m = re.search(r'type="([^"]+)"', tag)
        typ = (typ_m.group(1) if typ_m else "text").lower()
        val_m = re.search(r'value="([^"]*)"', tag)
        val = val_m.group(1) if val_m else ""
        if typ in ("submit", "button", "file"):
            continue
        if typ in ("checkbox", "radio") and not re.search(r"\bchecked\b", tag, re.I):
            continue
        pairs.append((name, val))
    for m in re.finditer(
        r'<select\b[^>]*name="([^"]+)"[^>]*>(.*?)</select>', region, re.I | re.S
    ):
        name, body = m.group(1), m.group(2)
        sel = re.search(r'<option\b[^>]*value="([^"]*)"[^>]*\bselected\b', body, re.I)
        opts = re.findall(r'<option\b[^>]*value="([^"]*)"', body, re.I)
        pairs.append((name, sel.group(1) if sel else (opts[0] if opts else "")))
    return pairs, checkstring(region) or ""


def merge_body(
    pairs: list[tuple[str, str]],
    overrides: dict[str, str],
    *,
    cs: str = "",
    ensure: dict[str, str] | None = None,
) -> str:
    """Replay do form: sobrescreve `overrides`, refresca CheckString se houver.

    `ensure`: campos que o POST precisa carregar mesmo se ausentes do form
    parseado (ex.: `FormName` no FlyingVoice, `Operate=Submit` no S3002). So
    sao adicionados quando ainda nao vistos.
    """
    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, val in pairs:
        if name in overrides:
            out_val = overrides[name]
        elif name == "CheckString" and cs:
            out_val = cs
        else:
            out_val = val
        merged.append((name, out_val))
        seen.add(name)
    for k, v in overrides.items():
        if k not in seen:
            merged.append((k, v))
            seen.add(k)
    for k, v in (ensure or {}).items():
        if k not in seen:
            merged.append((k, v))
    return "&".join(f"{quote(n, safe='')}={quote(v, safe='')}" for n, v in merged)


def http10_post(
    ip: str,
    path: str,
    body: str,
    *,
    headers: dict[str, str] | None = None,
    port: int = 80,
    timeout: float = 20.0,
) -> int:
    """POST cru em HTTP/1.0 (obrigatorio — o firmware nao fala 1.1 direito).

    Retorna o status HTTP. Levanta em erro de socket. `headers` extras
    (Cookie/Referer) sao injetados; Host/Content-* /Connection sao fixos.
    """
    extra = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    req = (
        f"POST {path} HTTP/1.0\r\n"
        f"Host: {ip}\r\n"
        f"{extra}"
        f"Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n{body}"
    )
    sk = socket.socket()
    sk.settimeout(timeout)
    try:
        sk.connect((ip, port))
        sk.sendall(req.encode("latin1"))
        resp = b""
        while True:
            chunk = sk.recv(4096)
            if not chunk:
                break
            resp += chunk
            if len(resp) > 65536:
                break
    finally:
        sk.close()
    m = re.match(rb"HTTP/1\.[01] (\d{3})", resp)
    return int(m.group(1)) if m else 0
