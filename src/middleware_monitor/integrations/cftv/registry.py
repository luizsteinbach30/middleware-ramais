"""Os adaptadores de CFTV desta versão, por ``adaptador_id`` — no molde do
``registry`` do configurador de ramais, mas fechado: a lista é esta, e o NOC só
vê o que o manifesto declara (``modulos``)."""

from __future__ import annotations

from .base import CftvAdapter
from .hikvision import HikvisionAdapter
from .intelbras import IntelbrasAdapter
from .unifi import UnifiAdapter

_REGISTRY: dict[str, CftvAdapter] = {}


def register_adapter(adapter: CftvAdapter) -> None:
    _REGISTRY[adapter.adaptador_id] = adapter


def get_adapter(adaptador_id: str) -> CftvAdapter:
    return _REGISTRY[adaptador_id]


def list_adapters() -> list[CftvAdapter]:
    return list(_REGISTRY.values())


def ids() -> tuple[str, ...]:
    return tuple(_REGISTRY)


for _adaptador in (IntelbrasAdapter(), HikvisionAdapter(), UnifiAdapter()):
    register_adapter(_adaptador)
