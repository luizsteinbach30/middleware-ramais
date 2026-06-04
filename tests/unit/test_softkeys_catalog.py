"""Catálogo de softkeys por fabricante (só tipos com encoding confirmado)."""

from __future__ import annotations

from middleware_monitor.domain.extension_configurator.softkeys import (
    softkey_catalog_for,
    vendor_of_model,
)


def test_vendor_detection() -> None:
    assert vendor_of_model("HTEK UC902G") == "htek"
    assert vendor_of_model("Intelbras V5501") == "intelbras"
    assert vendor_of_model("Yealink T31G") == "yealink"
    assert vendor_of_model("FlyingVoice P10") == "flyingvoice"
    assert vendor_of_model("desconhecido") == "htek"  # fallback


def test_flyingvoice_expoe_lista_ampla() -> None:
    cat = softkey_catalog_for("FlyingVoice P10")
    vals = {t["value"] for t in cat["types"]}
    assert {"dnd", "speed_dial", "history", "xml_group"} <= vals
    assert cat["vendor"] == "flyingvoice"
    assert all("value" in s and "label" in s for s in cat["key_slots"])


def test_htek_so_tipos_confirmados() -> None:
    cat = softkey_catalog_for("HTEK UC912")
    vals = {t["value"] for t in cat["types"]}
    assert vals == {"disabled", "line", "speed_dial", "blf"}


def test_slots_value_termina_em_numero() -> None:
    # os adapters extraem o índice do número final do nome → garante compat
    for modelo in ("HTEK UC902G", "Intelbras V5501", "Yealink T31G", "FlyingVoice P10"):
        for slot in softkey_catalog_for(modelo)["key_slots"]:
            assert slot["value"][-1].isdigit()
