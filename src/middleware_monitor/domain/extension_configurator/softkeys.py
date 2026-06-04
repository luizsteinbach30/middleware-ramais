"""Catálogo de teclas programáveis (softkeys) por fabricante.

A UI da config padrão usa este catálogo para mostrar, em cada ambiente, as
opções de tipo de tecla **do fabricante do modelo selecionado** — em vez de uma
lista genérica. Por enquanto lista SOMENTE os tipos cujo encoding já está
confirmado nos adapters (aplicáveis com segurança); cresce conforme exports/
manuais forem chegando (DND/captura/interfone/encaminhar nos demais, SoftKeys do
HTEK, etc.).

`key_slots`: o `value` termina sempre num número (os adapters extraem o índice
do número final do nome — `re.search(r"(\\d+)$")`), então mantemos `LineKeyN`
para compatibilidade com o que já está salvo, e o `label` mostra o nome nativo.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["is_intelbras_s_series", "softkey_catalog_for", "vendor_of_model"]

# Intelbras linha S (S3002, S3001...) usa firmware GoAhead — adapter proprio,
# distinto do V-series (RapidLogic). Casa "Intelbras S3002", "intelbras s3002".
_INTELBRAS_S_RE = re.compile(r"intelbras\s*s\d", re.IGNORECASE)


def is_intelbras_s_series(modelo: str) -> bool:
    return bool(_INTELBRAS_S_RE.search(modelo or ""))


def vendor_of_model(modelo: str) -> str:
    """Fabricante a partir do nome do modelo (independente do adapter_for)."""
    m = (modelo or "").lower()
    if m.startswith("intelbras"):
        return "intelbras_s3002" if is_intelbras_s_series(m) else "intelbras"
    if "flying" in m or "flyong" in m:
        return "flyingvoice"
    if m.startswith("yealink"):
        return "yealink"
    return "htek"


def _slots(prefix: str, n: int) -> list[dict[str, str]]:
    # value LineKeyN (compat com dados salvos); label com o nome nativo do fabricante.
    return [{"value": f"LineKey{i}", "label": f"{prefix} {i}"} for i in range(1, n + 1)]


def _types(pairs: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"value": v, "label": label} for v, label in pairs]


# Tipos confirmados por fabricante (alinhados aos mapas dos adapters).
# Contas SIP selecionáveis por fabricante (qual conta o aparelho registra).
# Só inclui [1, 2] onde o mapeamento da Account 2 está confirmado; senão [1].
_ACCOUNTS: dict[str, list[int]] = {
    "htek": [1],
    "intelbras": [1],
    "intelbras_s3002": [1],  # conta 2 (AccountID=1) ainda nao validada em hardware
    "yealink": [1, 2],   # confirmado (account.2.*)
    "flyingvoice": [1],
}


_CATALOG: dict[str, dict[str, Any]] = {
    "htek": {
        "vendor_label": "HTEK",
        "key_slots": _slots("Tecla de Linha", 4),
        "types": _types([
            ("disabled", "N/A"),
            ("line", "Linha SIP"),
            ("speed_dial", "Discagem rápida"),
            ("blf", "BLF (monitorar ramal)"),
        ]),
    },
    "intelbras": {
        "vendor_label": "Intelbras",
        "key_slots": _slots("Tecla DSS", 2),
        "types": _types([
            ("disabled", "Nenhum"),
            ("line", "Conta (linha)"),
            ("speed_dial", "Tecla de memória — Discagem rápida"),
            ("blf", "Lista BLF"),
        ]),
    },
    "intelbras_s3002": {
        "vendor_label": "Intelbras S3002",
        # ptype confirmado no lang pack do S3002 (br.js / SetSelectText).
        "key_slots": _slots("Tecla", 10),
        "types": _types([
            ("line", "Linha"),
            ("speed_dial", "Discagem rápida"),
            ("blf", "BLF"),
            ("blf_list", "Broadsoft BLF"),
        ]),
    },
    "yealink": {
        "vendor_label": "Yealink",
        "key_slots": _slots("Line Key", 2),
        "types": _types([
            ("disabled", "N/A"),
            ("line", "Linha"),
            ("speed_dial", "Discagem rápida"),
            ("blf", "BLF"),
            ("blf_list", "Lista BLF"),
        ]),
    },
    "flyingvoice": {
        "vendor_label": "FlyingVoice",
        "key_slots": _slots("SoftKey", 4),
        "types": _types([
            ("disabled", "N/A"),
            ("speed_dial", "Discagem rápida"),
            ("dnd", "DND"),
            ("history", "Histórico"),
            ("local_group", "Grupo local"),
            ("directory", "Diretório"),
            ("ldap", "LDAP"),
            ("menu", "Menu"),
            ("status", "Status"),
            ("xml_browser", "Navegador XML"),
            ("paging", "Paging"),
            ("paging_list", "Lista de Paging"),
            ("account_up", "Alternar conta acima"),
            ("account_down", "Alternar conta abaixo"),
            ("corp_directory", "Diretório corporativo"),
            ("usb_record", "Gravação USB"),
            ("xml_group", "XML Group"),
        ]),
    },
}


def softkey_catalog_for(modelo: str) -> dict[str, Any]:
    """Catálogo (vendor, key_slots, types) para o fabricante do modelo dado."""
    vendor = vendor_of_model(modelo)
    cat = _CATALOG.get(vendor, _CATALOG["htek"])
    return {
        "vendor": vendor,
        "vendor_label": cat["vendor_label"],
        "key_slots": cat["key_slots"],
        "types": cat["types"],
        "accounts": _ACCOUNTS.get(vendor, [1]),
    }
