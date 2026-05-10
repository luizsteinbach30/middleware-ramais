"""Pydantic schemas for the devices domain."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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


class DevicesPage(BaseModel):
    items: list[DeviceOut]
    total: int
    page: int
    size: int


class DevicePingOut(BaseModel):
    timestamp: datetime
    online: bool
    latency_ms: int | None


class HistoryPoint(BaseModel):
    timestamp: datetime
    online_ratio: float = Field(ge=0.0, le=1.0)
    latency_ms_avg: float | None
    latency_ms_max: float | None


class HistoryWindow(BaseModel):
    granularity: Literal["1m", "5m", "1h"]
    points: list[HistoryPoint]
