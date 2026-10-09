"""Selects the OS-specific implementation of each probe."""

from __future__ import annotations

import platform

from middleware_monitor.integrations.network.base import (
    ArpProbe,
    Fingerprinter,
    HttpProbe,
    PingProbe,
    TcpProbe,
)


def _is_windows() -> bool:
    return platform.system().lower().startswith("win")


def make_ping_probe() -> PingProbe:
    if _is_windows():
        from middleware_monitor.integrations.network.windows import WindowsPingProbe
        return WindowsPingProbe()
    from middleware_monitor.integrations.network.linux import LinuxPingProbe
    return LinuxPingProbe()


def make_arp_probe() -> ArpProbe:
    if _is_windows():
        from middleware_monitor.integrations.network.windows import WindowsArpProbe
        return WindowsArpProbe()
    from middleware_monitor.integrations.network.linux import LinuxArpProbe
    return LinuxArpProbe()


def make_fingerprinter() -> Fingerprinter:
    if _is_windows():
        from middleware_monitor.integrations.network.windows import HttpFingerprinter as W
        return W()
    from middleware_monitor.integrations.network.linux import HttpFingerprinter as L
    return L()


def make_tcp_probe() -> TcpProbe:
    # Biblioteca padrão, igual nos dois sistemas.
    from middleware_monitor.integrations.network.servicos import SondaTcp
    return SondaTcp()


def make_http_probe() -> HttpProbe:
    from middleware_monitor.integrations.network.servicos import SondaHttp
    return SondaHttp()
