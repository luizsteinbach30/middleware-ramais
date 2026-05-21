from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VendorCredentials:
    """Credenciais para acessar a interface administrativa do telefone."""

    username: str
    password: str


@dataclass
class DiscoveryResult:
    """Resultado de uma operação de descoberta de fabricante/modelo via IP."""

    vendor: str  # ex: "htek", "intelbras", "yealink", "fanvil", "flayvoice"
    model: str | None = None  # ex: "UC912", "UC924"
    firmware: str | None = None
    mac: str | None = None
    confidence: float = 0.0  # 0.0 a 1.0
    raw: dict[str, Any] | None = None  # dados crus para debug


class VendorAdapter(ABC):
    """Contrato que cada fabricante deve implementar.

    O fluxo de provisionamento é:
      1. fingerprint(ip)               — só rede, sem auth: vendor é nosso?
      2. discover(ip, creds)           — autenticado: descobre modelo/firmware/MAC
      3. generate_config(template,row) — produz bytes do arquivo de config
      4. send_config(ip, creds, cfg)   — envia para o aparelho
      5. backup_config(ip, creds)      — opcional: lê config antes de aplicar
    """

    vendor_id: str  # identificador único do fabricante (lowercase)

    @abstractmethod
    async def fingerprint(self, ip: str) -> float:
        """Retorna confianca (0.0-1.0) de que o IP eh deste fabricante. Sem auth."""

    @abstractmethod
    async def discover(self, ip: str, creds: VendorCredentials) -> DiscoveryResult:
        """Descobre modelo/firmware/MAC. Pode levantar exceção em falha de auth."""

    @abstractmethod
    def generate_config(self, template: dict[str, Any], row: dict[str, Any]) -> bytes:
        """Gera o arquivo de configuração a partir do modelo padrão + dados da linha do ambiente."""

    @abstractmethod
    async def send_config(
        self, ip: str, creds: VendorCredentials, cfg: bytes, *, fmt: str = "xml",
    ) -> None:
        """Envia o arquivo de configuracao ao telefone. Pode reiniciar o aparelho.

        `fmt`: formato do payload (`xml` padrao). HTEK aceita tambem `bin`.
        """

    async def backup_config(self, ip: str, creds: VendorCredentials) -> bytes | None:
        """Lê a configuração atual antes de aplicar a nova. Default: não suportado."""
        return None
