"""Time helpers shared by API serializers.

All timestamps in the DB are stored as naive datetimes in UTC. When exposing
them through the API we attach a ``Z`` suffix so the frontend (and any other
consumer) can parse them as ``Date`` objects and render in the user's local
timezone instead of treating the bare string as local time — which used to
make the UI show clocks off by the operator's UTC offset.
"""

from __future__ import annotations

from datetime import UTC, datetime

LOCAL_FMT = "%Y-%m-%d %H:%M:%S"


def iso_utc(value: datetime | None) -> str | None:
    """Serialize a (typically naive UTC) datetime as an ISO-8601 string with
    explicit UTC marker ``Z`` and second precision. Returns ``None`` if the
    input is ``None``."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def now_local_str() -> str:
    """Current local wall-clock time as ``'YYYY-MM-DD HH:MM:SS'``.

    Used for webhook payloads, where the receiving application expects a
    plain local timestamp (no ``T`` separator, no timezone marker)."""
    return datetime.now().strftime(LOCAL_FMT)


def as_local_str(value: datetime | None) -> str | None:
    """Convert a (typically naive UTC) datetime to local wall-clock time as
    ``'YYYY-MM-DD HH:MM:SS'``. Returns ``None`` if the input is ``None``."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime(LOCAL_FMT)
