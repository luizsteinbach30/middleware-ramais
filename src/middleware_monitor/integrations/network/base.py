"""Network probe protocols (typing only) and shared helpers."""

from __future__ import annotations

import ipaddress
import re
from typing import Protocol

_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}([-:][0-9a-fA-F]{2}){5}$")


def is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().replace("-", ":").lower()
    if not _MAC_RE.match(value.strip()):
        return None
    return cleaned


class PingProbe(Protocol):
    async def ping(self, ip: str, timeout_ms: int) -> int | None:
        """Returns latency in milliseconds when the host responded, else ``None``."""


class ArpProbe(Protocol):
    async def lookup(self, ip: str) -> str | None:
        """Returns the MAC address (normalized aa:bb:cc:dd:ee:ff) or ``None``."""


class Fingerprinter(Protocol):
    async def detect(self, ip: str) -> str | None:
        """Returns a manufacturer hint (Yealink, Fanvil, ...) or ``None``."""
