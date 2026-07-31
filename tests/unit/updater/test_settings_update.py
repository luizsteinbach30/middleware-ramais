"""Settings do updater — default do repo e precedência do token."""

from __future__ import annotations

import pytest

from middleware_monitor import _embedded
from middleware_monitor.settings import Settings


def test_update_repo_default_correto() -> None:
    # O default antigo era o placeholder "org/middleware-monitor" — com ele o
    # updater consultava um repo inexistente e nunca via release nenhuma.
    s = Settings(_env_file=None)
    assert s.update_repo == "luizsteinbach30/middleware-ramais"


def test_token_env_tem_precedencia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_embedded, "EMBEDDED_UPDATE_TOKEN_B64", "Z2hwX2VtYnV0aWRv")
    s = Settings(_env_file=None, update_token="ghp_do_env")
    assert s.effective_update_token == "ghp_do_env"


def test_token_embutido_usado_sem_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # base64 de "ghp_embutido"
    monkeypatch.setattr(_embedded, "EMBEDDED_UPDATE_TOKEN_B64", "Z2hwX2VtYnV0aWRv")
    s = Settings(_env_file=None, update_token="")
    assert s.effective_update_token == "ghp_embutido"


def test_sem_token_nenhum_retorna_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_embedded, "EMBEDDED_UPDATE_TOKEN_B64", "")
    s = Settings(_env_file=None, update_token="")
    assert s.effective_update_token is None


def test_embedded_token_b64_invalido_vira_vazio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_embedded, "EMBEDDED_UPDATE_TOKEN_B64", "%%%nao-e-base64%%%")
    assert _embedded.embedded_update_token() == ""
