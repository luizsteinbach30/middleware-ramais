"""Windows implementations of the network probes."""

from __future__ import annotations

import asyncio
import re
import subprocess

import httpx

from middleware_monitor.integrations.network.base import (
    is_valid_ip,
    normalize_mac,
)
from middleware_monitor.integrations.network.linux import _FINGERPRINTS

# Accept both English ("time=") and pt-BR ("tempo=") output.
_PING_TIME_RE = re.compile(r"(?:time|tempo)[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)
_TTL_RE = re.compile(r"TTL=", re.IGNORECASE)
_ARP_LINE_RE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})",
)

# Required so children (ping.exe, arp.exe) don't flash a console window when
# the parent is a windowed PyInstaller .exe with no attached console.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class WindowsPingProbe:
    async def ping(self, ip: str, timeout_ms: int) -> int | None:
        if not is_valid_ip(ip):
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping",
                "-n",
                "1",
                "-w",
                str(int(timeout_ms)),
                ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=(timeout_ms / 1000) + 1.5,
            )
        except (TimeoutError, FileNotFoundError, OSError):
            return None
        text = stdout.decode("utf-8", errors="ignore")
        if not _TTL_RE.search(text):
            return None
        m = _PING_TIME_RE.search(text)
        if not m:
            return 1  # responded but couldn't parse — assume sub-ms
        try:
            return round(float(m.group(1)))
        except ValueError:
            return None


class WindowsArpProbe:
    async def lookup(self, ip: str) -> str | None:
        if not is_valid_ip(ip):
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                "arp",
                "-a",
                ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        except (TimeoutError, FileNotFoundError, OSError):
            return None
        for line in stdout.decode("utf-8", errors="ignore").splitlines():
            m = _ARP_LINE_RE.match(line)
            if m and m.group(1) == ip:
                return normalize_mac(m.group(2))
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
