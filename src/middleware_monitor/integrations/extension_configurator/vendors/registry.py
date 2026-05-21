from __future__ import annotations

from .base import DiscoveryResult, VendorAdapter

_REGISTRY: dict[str, VendorAdapter] = {}


def register_adapter(adapter: VendorAdapter) -> None:
    _REGISTRY[adapter.vendor_id] = adapter


def get_adapter(vendor_id: str) -> VendorAdapter:
    return _REGISTRY[vendor_id]


def list_adapters() -> list[VendorAdapter]:
    return list(_REGISTRY.values())


async def discover_vendor(ip: str) -> DiscoveryResult | None:
    """Tenta todos os adapters registrados e retorna o de maior confiança (>= 0.5)."""
    best: tuple[float, VendorAdapter] | None = None
    for adapter in _REGISTRY.values():
        try:
            score = await adapter.fingerprint(ip)
        except Exception:
            score = 0.0
        if score >= 0.5 and (best is None or score > best[0]):
            best = (score, adapter)
    if best is None:
        return None
    return DiscoveryResult(vendor=best[1].vendor_id, confidence=best[0])
