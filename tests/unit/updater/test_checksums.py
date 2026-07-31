"""Parsing e verificação de SHA256SUMS (updater/checksums.py)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from middleware_monitor.updater.checksums import (
    ChecksumMismatch,
    hash_file,
    parse_sha256sums,
    verify_file,
)


def _write_sums(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "SHA256SUMS"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_parse_encontra_hash_por_nome(tmp_path: Path) -> None:
    sums = _write_sums(
        tmp_path,
        [
            "aaaa  app-v1.0.0.tar.gz",
            "bbbb  MiddlewareMonitor-1.0.0.exe",
            "cccc *binario-com-asterisco.bin",
        ],
    )
    assert parse_sha256sums(sums, "MiddlewareMonitor-1.0.0.exe") == "bbbb"
    assert parse_sha256sums(sums, "binario-com-asterisco.bin") == "cccc"


def test_parse_nome_ausente_levanta(tmp_path: Path) -> None:
    sums = _write_sums(tmp_path, ["aaaa  outro.tar.gz"])
    with pytest.raises(ChecksumMismatch):
        parse_sha256sums(sums, "inexistente.exe")


def test_verify_file_ok(tmp_path: Path) -> None:
    target = tmp_path / "app.bin"
    target.write_bytes(b"conteudo")
    digest = hashlib.sha256(b"conteudo").hexdigest()
    sums = _write_sums(tmp_path, [f"{digest}  app.bin"])
    verify_file(target, sums)  # não levanta
    assert hash_file(target) == digest


def test_verify_file_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "app.bin"
    target.write_bytes(b"conteudo")
    sums = _write_sums(tmp_path, ["0" * 64 + "  app.bin"])
    with pytest.raises(ChecksumMismatch):
        verify_file(target, sums)


def test_verify_file_target_name_explicito(tmp_path: Path) -> None:
    target = tmp_path / "download.tmp"
    target.write_bytes(b"abc")
    digest = hashlib.sha256(b"abc").hexdigest()
    sums = _write_sums(tmp_path, [f"{digest}  MiddlewareMonitor-2.0.0.exe"])
    verify_file(target, sums, target_name="MiddlewareMonitor-2.0.0.exe")
