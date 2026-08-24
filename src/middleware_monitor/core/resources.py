"""Rede de segurança para os arquivos web quando o app roda empacotado.

O PyInstaller *onefile* extrai o bundle para uma pasta temporária
(``sys._MEIPASS``) a cada execução, e essa pasta **some debaixo do processo em
execução** com mais frequência do que se gostaria: antivírus com quarentena
ativa em ``%TEMP%``, Sensor de Armazenamento do Windows numa execução longa, ou
um update parcial. Relatado em campo (2026-08-21) como
``TemplateNotFound: 'system_updates.html'`` — com o arquivo comprovadamente
dentro do executável.

A correção de raiz é empacotar em *onedir* (sem extração para ``%TEMP%``), que
muda o formato de distribuição e é decisão de release. Enquanto isso, este
módulo faz o app sobreviver ao sumiço: no boot, com o processo congelado, os
templates e os arquivos estáticos são lidos para memória (211 KB + 1,5 MB) e
servem de fallback quando o disco falhar.

Em desenvolvimento **não** há cache: editar um template e dar refresh precisa
continuar funcionando.
"""

from __future__ import annotations

import sys
from pathlib import Path

from middleware_monitor.core.logging import get_logger

log = get_logger("resources")

# Arquivos conferidos pela sonda periódica. Poucos e representativos: se estes
# sumiram, o diretório de extração foi mexido.
_CANARIOS = (
    "templates/base.html",
    "templates/system_updates.html",
    "static/js/api.js",
)

_templates_cache: dict[str, str] = {}
_static_cache: dict[str, bytes] = {}
_carregado = False


def empacotado() -> bool:
    """True quando rodando de dentro do executável PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def web_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "web"


def preload() -> None:
    """Lê templates e estáticos para memória. Só faz algo no executável."""
    global _carregado
    if _carregado or not empacotado():
        return
    base = web_dir()
    for arquivo in (base / "templates").rglob("*"):
        if arquivo.is_file():
            try:
                rel = arquivo.relative_to(base / "templates").as_posix()
                _templates_cache[rel] = arquivo.read_text(encoding="utf-8")
            except OSError as exc:  # pragma: no cover - disco
                log.warning("preload_template_falhou", arquivo=str(arquivo), erro=str(exc))
    for arquivo in (base / "static").rglob("*"):
        if arquivo.is_file():
            try:
                rel = arquivo.relative_to(base / "static").as_posix()
                _static_cache[rel] = arquivo.read_bytes()
            except OSError as exc:  # pragma: no cover - disco
                log.warning("preload_static_falhou", arquivo=str(arquivo), erro=str(exc))
    _carregado = True
    log.info(
        "web_resources_preloaded",
        templates=len(_templates_cache),
        estaticos=len(_static_cache),
        bytes_estaticos=sum(len(v) for v in _static_cache.values()),
    )


def templates_cache() -> dict[str, str]:
    return _templates_cache


def static_bytes(rel_path: str) -> bytes | None:
    return _static_cache.get(rel_path.replace("\\", "/").lstrip("/"))


def verificar_integridade() -> list[str]:
    """Nomes dos arquivos-canário que sumiram do disco (vazio = tudo certo).

    É o diagnóstico que faltava para fechar o caso do `TemplateNotFound`: em vez
    de descobrir pelo erro na tela do operador, o log diz a hora exata em que o
    diretório de extração foi mexido.
    """
    if not empacotado():
        return []
    base = web_dir()
    sumidos = [rel for rel in _CANARIOS if not (base / rel).exists()]
    if sumidos:
        log.error(
            "recursos_do_bundle_sumiram",
            arquivos=sumidos,
            base=str(base),
            detalhe=(
                "o diretorio de extracao do executavel foi alterado durante a "
                "execucao (antivirus, limpeza de %TEMP% ou update parcial). O "
                "app segue de pe pelo cache em memoria; exclua a pasta do "
                "antivirus e reinicie"
            ),
        )
    return sumidos


def reset_para_testes() -> None:
    global _carregado
    _templates_cache.clear()
    _static_cache.clear()
    _carregado = False
