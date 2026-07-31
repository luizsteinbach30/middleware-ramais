"""Parsing e verificação de SHA256SUMS, compartilhados pelos dois caminhos
de update (tarball legacy em ``installer.py`` e .exe standalone em
``standalone.py``)."""

from __future__ import annotations

import hashlib
from pathlib import Path


class ChecksumMismatch(Exception):
    pass


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sha256sums(path: Path, target_name: str) -> str:
    """Retorna o hash esperado de ``target_name`` num arquivo SHA256SUMS.

    Aceita o formato do ``sha256sum`` GNU (``<hash>  <nome>`` com marcador
    binário ``*`` opcional). Levanta :class:`ChecksumMismatch` se o nome não
    estiver listado."""
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("*") == target_name:
            return parts[0].lower()
    raise ChecksumMismatch(f"hash for {target_name!r} not found in SHA256SUMS")


def verify_file(path: Path, sums_path: Path, *, target_name: str | None = None) -> None:
    """Confere o SHA256 de ``path`` contra o SHA256SUMS em ``sums_path``.

    Levanta :class:`ChecksumMismatch` em divergência ou nome ausente."""
    expected = parse_sha256sums(sums_path, target_name or path.name)
    actual = hash_file(path)
    if actual.lower() != expected.lower():
        raise ChecksumMismatch(f"expected {expected}, got {actual}")
