"""Tests for the config repository — secrets are masked, plaintext only via load_secret."""

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


def test_secret_round_trip(db) -> None:
    update_config(
        db,
        AppConfigUpdate(uscall_host="uscall.test", uscall_token="my-secret-token"),
        user_id=None,
    )
    cfg = load_config(db)
    assert cfg.uscall_host == "uscall.test"
    assert cfg.uscall_token == "set"  # masked
    assert load_secret(db, "uscall_token") == "my-secret-token"


def test_clearing_secret(db) -> None:
    update_config(db, AppConfigUpdate(uscall_token="some"), user_id=None)
    update_config(db, AppConfigUpdate(uscall_token=""), user_id=None)
    cfg = load_config(db)
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
