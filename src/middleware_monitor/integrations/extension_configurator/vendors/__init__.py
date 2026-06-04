from .base import DiscoveryResult, VendorAdapter, VendorAuthError, VendorCredentials
from .flyingvoice import FlyingVoiceAdapter
from .htek import HTEKAdapter
from .intelbras import IntelbrasAdapter
from .intelbras_s3002 import IntelbrasS3002Adapter
from .registry import (
    discover_vendor,
    get_adapter,
    list_adapters,
    register_adapter,
)
from .yealink import YealinkAdapter


def register_default_adapters() -> None:
    """Idempotente: registra HTEK + Intelbras (V-series e S3002) + FlyingVoice + Yealink."""
    for adapter in (
        HTEKAdapter(), IntelbrasAdapter(), IntelbrasS3002Adapter(),
        FlyingVoiceAdapter(), YealinkAdapter(),
    ):
        register_adapter(adapter)


__all__ = [
    "DiscoveryResult",
    "FlyingVoiceAdapter",
    "HTEKAdapter",
    "IntelbrasAdapter",
    "IntelbrasS3002Adapter",
    "VendorAdapter",
    "VendorAuthError",
    "VendorCredentials",
    "YealinkAdapter",
    "discover_vendor",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    "register_default_adapters",
]
