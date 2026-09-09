"""Aviso de fuso desconhecido sai uma vez por processo, não uma vez por linha.

No host do cliente (Ubuntu em UTC, ambientes com hora "herdar") o log mostrava
``intelbras_timezone_desconhecido`` a cada abertura de planilha — e todo
WARNING vira registro em ``system_logs``. ``generate_config`` roda por linha e
por GET; sem limite, dezenas de inserts por abertura, sem informação nova.
"""

from __future__ import annotations

import pytest

from middleware_monitor.integrations.extension_configurator.vendors import base, htek, intelbras


@pytest.fixture(autouse=True)
def _reset():
    base.reset_avisos_para_testes()
    yield
    base.reset_avisos_para_testes()


def test_intelbras_avisa_uma_vez_por_fuso(monkeypatch: pytest.MonkeyPatch) -> None:
    avisos: list[dict] = []
    monkeypatch.setattr(
        intelbras.log, "warning", lambda evento, **kw: avisos.append({"evento": evento, **kw}),
    )
    template = {"ntp_server": "a.ntp.br", "timezone": "Etc/UTC", "timezone_offset_minutes": 0}
    for _ in range(30):  # 30 linhas de uma planilha
        xml = intelbras.IntelbrasAdapter._render_date(template)
        assert "<SNTPServer>a.ntp.br</SNTPServer>" in xml
        assert "<TimeZone>" not in xml  # fuso desconhecido: só o NTP, sem chute
    assert len(avisos) == 1
    assert avisos[0]["evento"] == "intelbras_timezone_desconhecido"
    assert avisos[0]["timezone"] == "Etc/UTC"


def test_intelbras_fuso_diferente_avisa_de_novo(monkeypatch: pytest.MonkeyPatch) -> None:
    avisos: list[str] = []
    monkeypatch.setattr(intelbras.log, "warning", lambda evento, **kw: avisos.append(kw["timezone"]))
    intelbras.IntelbrasAdapter._render_date({"timezone": "Etc/UTC", "timezone_offset_minutes": 0})
    intelbras.IntelbrasAdapter._render_date({"timezone": "Europe/Lisbon", "timezone_offset_minutes": 60})
    intelbras.IntelbrasAdapter._render_date({"timezone": "Etc/UTC", "timezone_offset_minutes": 0})
    assert avisos == ["Etc/UTC", "Europe/Lisbon"]


def test_intelbras_fuso_conhecido_nao_avisa(monkeypatch: pytest.MonkeyPatch) -> None:
    avisos: list[str] = []
    monkeypatch.setattr(intelbras.log, "warning", lambda evento, **kw: avisos.append(evento))
    xml = intelbras.IntelbrasAdapter._render_date(
        {"timezone": "America/Sao_Paulo", "timezone_offset_minutes": -180},
    )
    assert "<TimeZone>-12</TimeZone>" in xml
    assert avisos == []


def test_htek_avisa_uma_vez_por_fuso(monkeypatch: pytest.MonkeyPatch) -> None:
    avisos: list[dict] = []
    monkeypatch.setattr(htek.log, "warning", lambda evento, **kw: avisos.append({"evento": evento, **kw}))
    for _ in range(30):
        assert htek.HTEKAdapter._timezone_id("Etc/Nowhere", 12345) is None
    assert len(avisos) == 1
    assert avisos[0]["evento"] == "htek_timezone_desconhecido"


def test_avisar_uma_vez_e_por_evento_e_chave() -> None:
    class _Log:
        def __init__(self) -> None:
            self.chamadas: list[tuple[str, dict]] = []

        def warning(self, evento: str, **kw) -> None:
            self.chamadas.append((evento, kw))

    log = _Log()
    base.avisar_uma_vez(log, "k1", "ev", a=1)
    base.avisar_uma_vez(log, "k1", "ev", a=2)  # mesma chave: calado
    base.avisar_uma_vez(log, "k2", "ev", a=3)  # chave nova: fala
    base.avisar_uma_vez(log, "k1", "outro", a=4)  # evento novo: fala
    assert [c[0] for c in log.chamadas] == ["ev", "ev", "outro"]
    assert log.chamadas[0][1] == {"a": 1}
