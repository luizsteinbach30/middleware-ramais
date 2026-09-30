"""Network probe protocols (typing only) and shared helpers."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Protocol

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


# --- Disponibilidade de equipamento (expansão do NOC, Plano 0) -----------------------------
#
# Só leitura: abrir uma conexão TCP e fechar, e um GET sem corpo. Nenhuma sonda
# escreve no equipamento.


@dataclass(frozen=True)
class ResultadoDaSonda:
    """``valor`` é a latência em ms quando ``ok``; ``erro`` diz por que não foi."""

    ok: bool
    valor: int | None = None
    erro: str | None = None


class TcpProbe(Protocol):
    async def conectar(self, host: str, porta: int, timeout_s: float) -> ResultadoDaSonda:
        """A porta aceita conexão? Abre e fecha, sem mandar nada."""


class HttpProbe(Protocol):
    async def pedir(self, parametros: dict[str, Any], host: str, timeout_s: float) -> ResultadoDaSonda:
        """Um GET, sem corpo; ``ok`` só com o status esperado e resposta com forma."""
