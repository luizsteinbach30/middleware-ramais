"""Injeta o token de leitura de releases em ``src/middleware_monitor/_embedded.py``.

Rodado pelos jobs de build do release.yml ANTES de empacotar (tarball, .exe,
.run). Lê o token do env ``UPDATE_READ_TOKEN`` (secret do repositório) e
reescreve a constante ``EMBEDDED_UPDATE_TOKEN_B64`` com o base64 do valor.

Falha (exit 1) se o env estiver vazio — um build distribuído sem token não
consegue ler as releases do repo privado e nasce com auto-update quebrado.
Para builds locais/experimentais, defina ``ALLOW_EMPTY_UPDATE_TOKEN=1``.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "src" / "middleware_monitor" / "_embedded.py"
MARKER = 'EMBEDDED_UPDATE_TOKEN_B64 = ""'


def main() -> int:
    token = os.environ.get("UPDATE_READ_TOKEN", "").strip()
    if not token:
        if os.environ.get("ALLOW_EMPTY_UPDATE_TOKEN") == "1":
            print(
                "WARNING: UPDATE_READ_TOKEN vazio — build sem token embutido "
                "(auto-update dependerá de APP_UPDATE_TOKEN)."
            )
            return 0
        print(
            "ERROR: UPDATE_READ_TOKEN vazio. Configure o secret no repositório "
            "ou exporte ALLOW_EMPTY_UPDATE_TOKEN=1 para builds locais.",
            file=sys.stderr,
        )
        return 1

    text = TARGET.read_text(encoding="utf-8")
    if MARKER not in text:
        if 'EMBEDDED_UPDATE_TOKEN_B64 = "' in text:
            print(f"ERROR: {TARGET} ja contem um token injetado.", file=sys.stderr)
        else:
            print(f"ERROR: marcador nao encontrado em {TARGET}.", file=sys.stderr)
        return 1

    b64 = base64.b64encode(token.encode("ascii")).decode("ascii")
    TARGET.write_text(text.replace(MARKER, f'EMBEDDED_UPDATE_TOKEN_B64 = "{b64}"'), encoding="utf-8")
    print(f"Token de update embutido em {TARGET} ({len(token)} chars).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
