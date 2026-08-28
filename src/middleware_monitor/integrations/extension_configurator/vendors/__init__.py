from .base import (
    ACTION_NORMALIZE,
    ACTION_SET_IP,
    DEVICE_ACTIONS,
    ActionResult,
    DiscoveryResult,
    VendorActionUnsupported,
    VendorAdapter,
    VendorAuthError,
    VendorCredentials,
)
from .flyingvoice import FlyingVoiceAdapter
from .htek import HTEKAdapter
from .intelbras import IntelbrasAdapter
from .intelbras_s3002 import IntelbrasS3002Adapter
from .intelbras_tip125i import IntelbrasTIP125iAdapter
from .registry import (
    discover_vendor,
    get_adapter,
    list_adapters,
    register_adapter,
)
from .yealink import YealinkAdapter


def register_default_adapters() -> None:
    """Idempotente: registra HTEK + Intelbras (V-series, S3002 e TIP) + FlyingVoice + Yealink."""
    for adapter in (
        HTEKAdapter(), IntelbrasAdapter(), IntelbrasS3002Adapter(),
        IntelbrasTIP125iAdapter(), FlyingVoiceAdapter(), YealinkAdapter(),
    ):
        register_adapter(adapter)


__all__ = [
    "ACTION_NORMALIZE",
    "ACTION_SET_IP",
    "DEVICE_ACTIONS",
    "ActionResult",
    "DiscoveryResult",
    "FlyingVoiceAdapter",
    "HTEKAdapter",
    "IntelbrasAdapter",
    "IntelbrasS3002Adapter",
    "IntelbrasTIP125iAdapter",
    "VendorActionUnsupported",
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
