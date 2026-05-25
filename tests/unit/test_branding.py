"""Armazenamento da identidade visual (logo/favicon) no APP_DATA_DIR."""

from __future__ import annotations

from middleware_monitor import branding


def test_save_find_content_type_and_replace() -> None:
    assert branding.find_asset("logo") is None
    p = branding.save_asset("logo", ".png", b"abc")
    assert p.exists()
    assert branding.find_asset("logo") == p
    assert branding.content_type_for(p) == "image/png"

    # trocar por outra extensão remove a anterior
    p2 = branding.save_asset("logo", "svg", b"<svg/>")
    assert branding.find_asset("logo") == p2
    assert not p.exists()
    assert branding.content_type_for(p2) == "image/svg+xml"


def test_logo_for_pdf_so_raster() -> None:
    branding.save_asset("logo", ".svg", b"<svg/>")
    assert branding.logo_for_pdf() is None      # svg não embute no pdf
    branding.save_asset("logo", ".png", b"abc")
    assert branding.logo_for_pdf() is not None


def test_remove() -> None:
    branding.save_asset("favicon", ".ico", b"x")
    assert branding.find_asset("favicon") is not None
    assert branding.remove_asset("favicon") is True
    assert branding.find_asset("favicon") is None
    assert branding.remove_asset("favicon") is False


def test_allowed_ext() -> None:
    assert ".ico" in branding.allowed_ext("favicon")
    assert ".ico" not in branding.allowed_ext("logo")
    assert ".webp" in branding.allowed_ext("logo")
