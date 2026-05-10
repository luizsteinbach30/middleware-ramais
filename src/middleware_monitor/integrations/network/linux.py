"""Linux implementations of the network probes."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx

from middleware_monitor.integrations.network.base import (
    is_valid_ip,
    normalize_mac,
)

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
    async def ping(self, ip: str, timeout_ms: int) -> int | None:
        if not is_valid_ip(ip):
            return None
        timeout_s = max(1, int(round(timeout_ms / 1000)))
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
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 1)
        except (TimeoutError, FileNotFoundError, OSError):
            return None
        if proc.returncode != 0:
            return None
        m = _PING_TIME_RE.search(stdout.decode("utf-8", errors="ignore"))
        if not m:
            return None
        try:
            return int(round(float(m.group(1))))
        except ValueError:
            return None


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
