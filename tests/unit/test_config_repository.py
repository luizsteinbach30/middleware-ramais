"""Tests for the config repository — secrets are masked, plaintext only via load_secret.

Nota v2.7.0: uscall_host/uscall_token saíram do ``AppConfigUpdate`` (servidores
USCall vivem na tabela ``uscall_servers``); o round-trip de segredo do config
KV é coberto pelos webhooks.
"""

from __future__ import annotations

from middleware_monitor.domain.config.repository import (
    load_config,
    load_secret,
    update_config,
)
from middleware_monitor.domain.config.schemas import (
    AppConfigUpdate,
    WebhookConfigUpdate,
)


def test_webhook_secret_round_trip(db) -> None:
    update_config(
        db,
        AppConfigUpdate(webhooks={"devices": WebhookConfigUpdate(token="my-secret-token")}),
        user_id=None,
    )
    cfg = load_config(db)
    assert cfg.webhooks["devices"].token == "set"  # masked
    assert load_secret(db, "webhooks.devices.token") == "my-secret-token"


def test_clearing_webhook_secret(db) -> None:
    update_config(db, AppConfigUpdate(webhooks={"devices": WebhookConfigUpdate(token="some")}), user_id=None)
    update_config(db, AppConfigUpdate(webhooks={"devices": WebhookConfigUpdate(token="")}), user_id=None)
    cfg = load_config(db)
    assert cfg.webhooks["devices"].token is None


def test_uscall_kv_nao_recebe_mais_escrita(db) -> None:
    """Campos extra no payload são ignorados (pydantic extra=ignore) e o KV
    legado do USCall permanece intocado."""
    update_config(
        db,
        AppConfigUpdate.model_validate({"uscall_host": "x.test", "uscall_token": "t"}),
        user_id=None,
    )
    cfg = load_config(db)
    assert cfg.uscall_host == ""
    assert cfg.uscall_token is None


def test_webhook_partial_update(db) -> None:
    update_config(
        db,
        AppConfigUpdate(
            webhooks={
                "extensions": WebhookConfigUpdate(
                    enabled=True, url="https://x.test", token="bear"
                )
            }
        ),
        user_id=None,
    )
    cfg = load_config(db)
    assert cfg.webhooks["extensions"].enabled is True
    assert cfg.webhooks["extensions"].url == "https://x.test"
    assert cfg.webhooks["extensions"].token == "set"
