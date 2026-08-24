"""Sobrevivência ao sumiço dos arquivos do bundle (caso de campo 2026-08-21).

Um cliente rodando o `.exe` recebeu `TemplateNotFound` para um template que
**estava** dentro do executável: o diretório de extração em `%TEMP%` foi
esvaziado com o app no ar. Estes testes fixam as duas metades da mitigação — a
tela continua de pé pelo cache em memória, e o log denuncia a hora em que os
arquivos sumiram.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from middleware_monitor.core import resources


@pytest.fixture(autouse=True)
def _limpa_cache():
    resources.reset_para_testes()
    yield
    resources.reset_para_testes()


def test_do_codigo_fonte_nao_ha_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Em desenvolvimento, editar um template e dar refresh tem de funcionar —
    cache aqui só atrapalharia."""
    monkeypatch.delattr("sys.frozen", raising=False)
    resources.preload()
    assert resources.templates_cache() == {}
    assert resources.static_bytes("js/api.js") is None


def test_empacotado_carrega_templates_e_estaticos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.frozen", True, raising=False)
    resources.preload()

    cache = resources.templates_cache()
    assert "base.html" in cache
    assert "system_backup.html" in cache
    # subpasta entra com o caminho relativo, que é como o Jinja pede
    assert "extension_configurator/list.html" in cache
    assert resources.static_bytes("js/api.js") is not None


def test_template_sumido_do_disco_ainda_renderiza(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """É exatamente o caso relatado: o arquivo não está mais lá, e a tela
    responde assim mesmo."""
    monkeypatch.setattr("sys.frozen", True, raising=False)
    resources.preload()

    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    env = Environment(
        loader=ChoiceLoader([
            FileSystemLoader(str(tmp_path)),  # pasta vazia = disco "sumiu"
            DictLoader(resources.templates_cache()),
        ]),
    )
    assert env.get_template("system_updates.html") is not None


def test_sonda_denuncia_arquivos_sumidos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr(resources, "web_dir", lambda: tmp_path)

    assert sorted(resources.verificar_integridade()) == sorted(resources._CANARIOS)


def test_sonda_calada_quando_esta_tudo_no_lugar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.frozen", True, raising=False)
    assert resources.verificar_integridade() == []


def test_sonda_nao_roda_do_codigo_fonte(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sem executável não há diretório de extração para vigiar."""
    monkeypatch.delattr("sys.frozen", raising=False)
    monkeypatch.setattr(resources, "web_dir", lambda: tmp_path)
    assert resources.verificar_integridade() == []


def test_static_cai_para_a_memoria_quando_o_arquivo_some(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import asyncio

    from middleware_monitor.web.static_files import ResilientStaticFiles

    monkeypatch.setattr("sys.frozen", True, raising=False)
    resources.preload()

    # diretório vazio: qualquer request cai no fallback
    servidor = ResilientStaticFiles(directory=str(tmp_path))
    scope = {"type": "http", "method": "GET", "headers": []}
    resp = asyncio.run(servidor.get_response("js/api.js", scope))

    assert resp.status_code == 200
    assert b"export" in resp.body
    assert "javascript" in resp.media_type


def test_static_inexistente_continua_404(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import asyncio

    from starlette.exceptions import HTTPException as StarletteHTTPException

    from middleware_monitor.web.static_files import ResilientStaticFiles

    monkeypatch.setattr("sys.frozen", True, raising=False)
    resources.preload()
    servidor = ResilientStaticFiles(directory=str(tmp_path))
    scope = {"type": "http", "method": "GET", "headers": []}

    with pytest.raises(StarletteHTTPException):
        asyncio.run(servidor.get_response("js/nao-existe.js", scope))
