"""Webhook payload format.

The receiving application accepts the envelope only when ``data`` is a flat
array and ``timestamp`` is plain local wall-clock time (no ``T``/``Z``). These
tests pin that contract so the "não funciona" object shape can't come back.
"""

from __future__ import annotations

import json
import re

import httpx
import respx

from middleware_monitor.core.db import session_factory
from middleware_monitor.domain.config.repository import update_config
from middleware_monitor.domain.config.schemas import (
    AppConfigUpdate,
    WebhookConfigUpdate,
)
from middleware_monitor.domain.webhooks.sender import WebhookSender, _wrap_payload

# "2026-05-18 08:20:32" — local, no T separator, no Z marker.
_TS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

_DEVICE_KEYS = {"name", "ip", "logical_status", "status", "latency", "last_ping", "mac"}

_SAMPLE_DEVICES = [
    {
        "name": "3660",
        "ip": "10.20.30.40",
        "logical_status": "disponivel",
        "status": "online",
        "latency": 12,
        "last_ping": "2026-05-18 08:20:00",
        "mac": "00:11:22:33:44:55",
    },
]


def test_wrap_payload_keeps_data_as_array() -> None:
    env = _wrap_payload("devices", _SAMPLE_DEVICES, client_code="ACME", is_test=False)
    assert isinstance(env["data"], list)
    assert env["data"] == _SAMPLE_DEVICES
    assert env["event_type"] == "devices"
    assert env["client_code"] == "ACME"
    assert env["test"] is False


def test_wrap_payload_timestamp_is_plain_local() -> None:
    env = _wrap_payload("devices", [], client_code="X", is_test=False)
    ts = env["timestamp"]
    assert _TS.match(ts), ts
    assert "T" not in ts
    assert "Z" not in ts


@respx.mock
async def test_dispatch_devices_array_accepted_with_202(db) -> None:
    """A devices webhook posting a flat array is accepted (HTTP 202)."""
    route = respx.post("https://hook.test/devices").mock(
        return_value=httpx.Response(202, json={"queued": True})
    )
    update_config(
        db,
        AppConfigUpdate(
            client_code="ACME",
            webhooks={
                "devices": WebhookConfigUpdate(
                    enabled=True, url="https://hook.test/devices"
                )
            },
        ),
        user_id=None,
    )

    sender = WebhookSender(session_factory)
    event = await sender.dispatch("devices", _SAMPLE_DEVICES)

    assert event is not None
    assert event.success is True
    assert event.http_status == 202

    sent = json.loads(route.calls.last.request.content)
    assert isinstance(sent["data"], list)
    assert sent["data"] == _SAMPLE_DEVICES
    assert set(sent["data"][0]) == _DEVICE_KEYS
    assert _TS.match(sent["timestamp"])


@respx.mock
async def test_dispatch_test_sends_array(db) -> None:
    """``dispatch_test`` also produces an array payload, accepted as 200 OK."""
    route = respx.post("https://hook.test/devices").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    update_config(
        db,
        AppConfigUpdate(
            client_code="ACME",
            webhooks={
                "devices": WebhookConfigUpdate(
                    enabled=True, url="https://hook.test/devices"
                )
            },
        ),
        user_id=None,
    )

    sender = WebhookSender(session_factory)
    event = await sender.dispatch_test("devices")

    assert event is not None
    assert event.success is True
    assert event.http_status == 200

    sent = json.loads(route.calls.last.request.content)
    assert sent["test"] is True
    assert isinstance(sent["data"], list)
    assert set(sent["data"][0]) == _DEVICE_KEYS
