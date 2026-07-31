from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------- device actions
# Ações remotas nos telefones (v2.7.0). O catálogo é fechado; cada adapter
# declara em ``capabilities()`` o subconjunto que homologou.
#   normalize  — devolver o telefone ao estado operacional: volume no máximo +
#                DND desligado (o problema real: operador abaixa o volume/ativa
#                DND). Homologado ao vivo (ver docs/design/DEVICE_ACTIONS_HOMOLOGACAO.md).
#   set_ip     — trocar o endereço IP (ação PERIGOSA; a UI exige confirmação).
ACTION_NORMALIZE = "normalize"
ACTION_SET_IP = "set_ip"
DEVICE_ACTIONS: frozenset[str] = frozenset({ACTION_NORMALIZE, ACTION_SET_IP})


class VendorActionUnsupported(RuntimeError):
    """O adapter não suporta (ou não homologou) a ação pedida."""


@dataclass(slots=True)
class ActionResult:
    """Resultado de uma ação remota num telefone."""

    ok: bool
    detail: str = ""
    rebooted: bool = False  # a ação reinicia o aparelho (ex.: HTEK config)


class VendorAuthError(RuntimeError):
    """Levantada por um adapter quando o aparelho recusa as credenciais.

    Sinal semântico (independente do protocolo: HTTP 401/403 no HTEK, página de
    login recusada no Intelbras/FlyingVoice) para o pipeline saber que vale
    tentar a credencial seguinte da chain. Herda ``RuntimeError`` para que, se
    não for tratada, ainda seja persistida como erro normal da linha.
    """


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

    # ------------------------------------------------------------ device actions
    def capabilities(self) -> frozenset[str]:
        """Ações remotas homologadas por este adapter (subconjunto de
        ``DEVICE_ACTIONS``). Default: nenhuma — a UI oculta o que não está aqui."""
        return frozenset()

    async def execute_action(
        self, ip: str, creds: VendorCredentials, action: str, params: dict[str, Any],
    ) -> ActionResult:
        """Executa uma ação remota. Default: não suportado.

        Pode levantar :class:`VendorAuthError` (credencial recusada — o service
        tenta a próxima da chain) ou :class:`VendorActionUnsupported`.
        """
        raise VendorActionUnsupported(f"{self.vendor_id}: ação {action!r} não suportada")
