"""Regressão: o resumo de status do ambiente deve refletir `ultimo_status`
real ('ok'/'erro'/None), não os rótulos derivados ('applied'/'error')."""

from __future__ import annotations

from middleware_monitor.api.extension_configurator import _status_resumo
from middleware_monitor.core.models import Device, ExtensionLine
from middleware_monitor.domain.extension_configurator.service import line_status


def _line(status: str | None) -> ExtensionLine:
    ln = ExtensionLine()
    ln.ultimo_status = status
    return ln


def _line_com_device(logical: str) -> ExtensionLine:
    ln = ExtensionLine()
    ln.ultimo_status = None
    ln.device = Device(name="dev", logical_status=logical)
    return ln


def test_line_status_registrado_quando_device_available() -> None:
    # nunca aplicado, mas telefone registrado no PBX → OK, não pendente
    assert line_status(_line_com_device("available"), "h") == "registered"


def test_line_status_pendente_quando_device_indisponivel() -> None:
    assert line_status(_line_com_device("unavailable"), "h") == "pending"


def test_resumo_registrado_nao_conta_como_pendente() -> None:
    r = _status_resumo([_line_com_device("available"), _line(None)])
    assert r["registered"] == 1
    assert r["pending"] == 1  # o _line(None) sem device segue pendente


def test_resumo_conta_ok_como_applied() -> None:
    r = _status_resumo([_line("ok"), _line("ok")])
    assert r == {"applied": 2, "registered": 0, "pending": 0, "error": 0, "agregado": "ok"}


def test_resumo_mistura_ok_erro_pendente() -> None:
    r = _status_resumo([_line("ok"), _line("erro"), _line(None)])
    assert r["applied"] == 1
    assert r["error"] == 1
    assert r["pending"] == 1
    assert r["agregado"] == "erros"  # erro tem precedência


def test_resumo_pendentes_quando_nao_aplicado() -> None:
    r = _status_resumo([_line("ok"), _line(None)])
    assert r["agregado"] == "pendentes"
    assert r["pending"] == 1


def test_resumo_vazio() -> None:
    assert _status_resumo([])["agregado"] == "vazio"
