"""Armazenamento da identidade visual (logo + favicon) no APP_DATA_DIR.

Os arquivos ficam em ``<data_dir>/branding/<kind><ext>`` (um por tipo). A
presença do arquivo indica que está configurado — não há estado no banco.
"""

from __future__ import annotations

from pathlib import Path

from middleware_monitor.settings import get_settings

KINDS: tuple[str, str] = ("logo", "favicon")

LOGO_EXT = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
FAVICON_EXT = {".ico", ".png", ".svg"}
_CONTENT_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
}
# Formatos raster que o fpdf2 consegue embutir no PDF do relatório.
_PDF_EMBEDDABLE = {".png", ".jpg", ".jpeg", ".gif"}

MAX_BYTES = 2 * 1024 * 1024  # 2 MB


def branding_dir() -> Path:
    d = get_settings().data_dir / "branding"
    d.mkdir(parents=True, exist_ok=True)
    return d


def allowed_ext(kind: str) -> set[str]:
    return FAVICON_EXT if kind == "favicon" else LOGO_EXT


def find_asset(kind: str) -> Path | None:
    """Caminho do asset (logo/favicon) se existir, senão None."""
    matches = sorted(branding_dir().glob(f"{kind}.*"))
    return matches[0] if matches else None


def save_asset(kind: str, ext: str, data: bytes) -> Path:
    """Grava o asset, removendo qualquer versão anterior (qualquer extensão)."""
    ext = ext.lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    remove_asset(kind)
    path = branding_dir() / f"{kind}{ext}"
    path.write_bytes(data)
    return path


def remove_asset(kind: str) -> bool:
    removed = False
    for p in branding_dir().glob(f"{kind}.*"):
        p.unlink(missing_ok=True)
        removed = True
    return removed


def content_type_for(path: Path) -> str:
    return _CONTENT_TYPE.get(path.suffix.lower(), "application/octet-stream")


def logo_for_pdf() -> Path | None:
    """Logo se for um formato que o fpdf2 embute (raster). SVG fica de fora."""
    p = find_asset("logo")
    if p is not None and p.suffix.lower() in _PDF_EMBEDDABLE:
        return p
    return None
