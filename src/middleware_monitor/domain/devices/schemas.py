"""Pydantic schemas for the devices domain."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer

from middleware_monitor.core.time import iso_utc


class DeviceOut(BaseModel):
    id: int
    name: str
    ip: str | None
    mac: str | None
    model: str | None
    logical_status: str
    network_status: str
    latency_ms: int | None
    last_seen_at: datetime | None
    last_ping_at: datetime | None
    notes: str | None

    @field_serializer("last_seen_at", "last_ping_at", when_used="json-unless-none")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return iso_utc(value)


class DevicesPage(BaseModel):
    items: list[DeviceOut]
    total: int
    page: int
    size: int


class DevicePingOut(BaseModel):
    timestamp: datetime
    online: bool
    latency_ms: int | None

    @field_serializer("timestamp", when_used="json-unless-none")
    def _ser_ts(self, value: datetime) -> str:
        return iso_utc(value) or ""


class HistoryPoint(BaseModel):
    timestamp: datetime
    online_ratio: float = Field(ge=0.0, le=1.0)
    latency_ms_avg: float | None
    latency_ms_max: float | None

    @field_serializer("timestamp", when_used="json-unless-none")
    def _ser_ts(self, value: datetime) -> str:
        return iso_utc(value) or ""


class HistoryWindow(BaseModel):
    granularity: Literal["1m", "5m", "1h"]
    points: list[HistoryPoint]
