from .base import DiscoveryResult, VendorAdapter, VendorCredentials
from .htek import HTEKAdapter
from .intelbras import IntelbrasAdapter
from .registry import (
    discover_vendor,
    get_adapter,
    list_adapters,
    register_adapter,
)


def register_default_adapters() -> None:
    """Idempotente: registra HTEK + Intelbras se ainda nao estiverem."""
    for adapter in (HTEKAdapter(), IntelbrasAdapter()):
        register_adapter(adapter)


__all__ = [
    "DiscoveryResult",
    "HTEKAdapter",
    "IntelbrasAdapter",
    "VendorAdapter",
    "VendorCredentials",
    "discover_vendor",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    "register_default_adapters",
]
