"""Linux implementations of the network probes."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx

from middleware_monitor.core.logging import get_logger
from middleware_monitor.integrations.network.base import (
    is_valid_ip,
    normalize_mac,
)

log = get_logger("network.linux")

# Avisa uma vez por processo: o problema é do servidor, não muda por IP, e o
# monitor pinga centenas de aparelhos por rodada.
_ja_avisou_ping_indisponivel = False

_PING_TIME_RE = re.compile(r"time=([\d.]+)\s*ms", re.IGNORECASE)
_FINGERPRINTS = (
    ("yealink", "Yealink"),
    ("fanvil", "Fanvil"),
    ("grandstream", "Grandstream"),
    ("intelbras", "Intelbras"),
    ("htek", "Htek"),
    ("polycom", "Polycom"),
    ("audiocodes", "AudioCodes"),
)


class LinuxPingProbe:
    """``ping -c 1`` do iputils.

    ``ultimo_erro`` distingue **"o host não respondeu"** (``None`` — código de
    saída 1, normal para telefone desligado) de **"o servidor não consegue
    pingar ninguém"** (código ≥ 2: ``ping: socket: Operation not permitted``
    quando o sandbox do systemd tira a capability do binário, ``Network is
    unreachable`` sem rota, binário ausente). Antes os dois viravam "offline"
    em silêncio, e o operador ia conferir a rede do cliente quando o defeito
    estava no servidor (caso de campo 2026-09-09).
    """

    def __init__(self) -> None:
        self.ultimo_erro: str | None = None

    async def ping(self, ip: str, timeout_ms: int) -> int | None:
        if not is_valid_ip(ip):
            return None
        self.ultimo_erro = None
        timeout_s = max(1, round(timeout_ms / 1000))
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping",
                "-c",
                "1",
                "-W",
                str(timeout_s),
                ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 1)
        except FileNotFoundError:
            self._registrar("comando 'ping' não encontrado no servidor (instale o pacote iputils-ping)")
            return None
        except (TimeoutError, OSError):
            return None
        if proc.returncode != 0:
            # iputils: 1 = sem resposta (host offline); 2+ = a sonda falhou.
            if proc.returncode is not None and proc.returncode >= 2:
                detalhe = stderr.decode("utf-8", errors="ignore").strip().splitlines()
                self._registrar(detalhe[-1] if detalhe else f"ping saiu com código {proc.returncode}")
            return None
        m = _PING_TIME_RE.search(stdout.decode("utf-8", errors="ignore"))
        try:
            return round(float(m.group(1))) if m else None
        except ValueError:
            return None

    def _registrar(self, motivo: str) -> None:
        global _ja_avisou_ping_indisponivel
        self.ultimo_erro = motivo
        if not _ja_avisou_ping_indisponivel:
            _ja_avisou_ping_indisponivel = True
            log.warning(
                "ping_indisponivel",
                motivo=motivo,
                dica=(
                    "o servidor não consegue pingar — todo aparelho vai aparecer offline. "
                    "Confira permissão de ICMP (sysctl net.ipv4.ping_group_range, "
                    "capability do /usr/bin/ping sob NoNewPrivileges) e rota até a rede dos telefones."
                ),
            )


def reset_diagnostico_para_testes() -> None:
    global _ja_avisou_ping_indisponivel
    _ja_avisou_ping_indisponivel = False


class LinuxArpProbe:
    async def lookup(self, ip: str) -> str | None:
        if not is_valid_ip(ip):
            return None
        try:
            content = Path("/proc/net/arp").read_text(encoding="utf-8")
        except OSError:
            return None
        for line in content.splitlines()[1:]:
            cols = line.split()
            if len(cols) >= 4 and cols[0] == ip and cols[3] != "00:00:00:00:00:00":
                return normalize_mac(cols[3])
        return None


class HttpFingerprinter:
    async def detect(self, ip: str) -> str | None:
        if not is_valid_ip(ip):
            return None
        url = f"http://{ip}"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.head(url)
                if resp.status_code in (405, 501):
                    resp = await client.get(url)
        except (httpx.HTTPError, OSError):
            return None
        server = resp.headers.get("Server", "").lower()
        for needle, vendor in _FINGERPRINTS:
            if needle in server:
                return vendor
        return None
