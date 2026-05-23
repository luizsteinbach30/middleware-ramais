from .base import DiscoveryResult, VendorAdapter, VendorCredentials
from .flyingvoice import FlyingVoiceAdapter
from .htek import HTEKAdapter
from .intelbras import IntelbrasAdapter
from .registry import (
    discover_vendor,
    get_adapter,
    list_adapters,
    register_adapter,
)


def register_default_adapters() -> None:
    """Idempotente: registra HTEK + Intelbras + FlyingVoice se ainda nao estiverem."""
    for adapter in (HTEKAdapter(), IntelbrasAdapter(), FlyingVoiceAdapter()):
        register_adapter(adapter)


__all__ = [
    "DiscoveryResult",
    "FlyingVoiceAdapter",
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
