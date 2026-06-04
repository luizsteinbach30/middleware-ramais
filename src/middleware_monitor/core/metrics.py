"""Prometheus metrics — opt-in via ``APP_METRICS_ENABLED``.

If ``prometheus_client`` is not installed (it lives behind the
``[metrics]`` extra), this module gracefully degrades to no-ops so the rest of
the application keeps working.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        REGISTRY,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    HAS_PROMETHEUS = True
except ImportError:  # pragma: no cover
    HAS_PROMETHEUS = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _Noop:
        def __init__(self, *_: Any, **__: Any) -> None: ...

        def __getattr__(self, _: str) -> Any:
            return lambda *a, **kw: None

    Counter = Gauge = Histogram = _Noop  # type: ignore[assignment,misc]
    REGISTRY = _Noop()  # type: ignore[assignment]

    def generate_latest(*_: Any, **__: Any) -> bytes:  # type: ignore[misc]
        return b""


from middleware_monitor.version import __version__

if HAS_PROMETHEUS:
    APP_INFO = Gauge("mm_app_info", "Build info", ["version"])
    APP_INFO.labels(version=__version__).set(1)

    DEVICES_TOTAL = Gauge(
        "mm_devices_total",
        "Devices broken down by status",
        ["logical_status", "network_status"],
    )
    PING_TOTAL = Counter("mm_ping_total", "Ping outcomes", ["result"])
    PING_LATENCY = Histogram(
        "mm_ping_latency_ms",
        "Ping latency in milliseconds",
        buckets=(1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500),
    )

    COLLECT_DURATION = Histogram(
        "mm_collect_extensions_duration_seconds",
        "USCall collection duration",
        ["result"],
    )
    COLLECT_FAILURES = Counter(
        "mm_collect_extensions_failures_total",
        "USCall collection failures by reason",
        ["reason"],
    )

    WEBHOOK_ATTEMPTS = Counter(
        "mm_webhook_attempts_total",
        "Webhook attempts",
        ["event_type", "success"],
    )
    WEBHOOK_DURATION = Histogram(
        "mm_webhook_duration_seconds",
        "Webhook duration",
        ["event_type"],
    )

    UPDATE_CHECKS = Counter(
        "mm_update_check_total", "Update checks", ["result"]
    )
    UPDATE_APPLIES = Counter(
        "mm_update_apply_total", "Update applications", ["result"]
    )
else:  # pragma: no cover
    APP_INFO = DEVICES_TOTAL = PING_TOTAL = PING_LATENCY = _Noop()  # type: ignore[assignment]
    COLLECT_DURATION = COLLECT_FAILURES = WEBHOOK_ATTEMPTS = WEBHOOK_DURATION = _Noop()  # type: ignore[assignment]
    UPDATE_CHECKS = UPDATE_APPLIES = _Noop()  # type: ignore[assignment]


def render_text() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
