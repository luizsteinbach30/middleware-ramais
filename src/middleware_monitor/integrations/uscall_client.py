"""Async client for the USCall extension status API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx


class UscallError(Exception):
    pass


class UscallAuthError(UscallError):
    pass


class UscallNetworkError(UscallError):
    pass


class UscallProtocolError(UscallError):
    pass


@dataclass(slots=True)
class UscallTestResult:
    success: bool
    http_status: int | None
    latency_ms: int | None
    error: str | None


class UscallClient:
    """Talks to the legacy USCall ``/api/extenstatus`` endpoint."""

    def __init__(self, host: str, token: str, *, verify_ssl: bool = True, timeout: float = 10.0) -> None:
        if not host or not token:
            raise ValueError("host and token are required")
        self.host = host.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = timeout

    @property
    def _url(self) -> str:
        return f"https://{self.host}/api/extenstatus"

    async def fetch_extensions(self) -> list[dict[str, Any]]:
        params = {"token": self.token, "tipo": "all"}
        try:
            async with httpx.AsyncClient(verify=self.verify_ssl, timeout=self.timeout) as client:
                resp = await client.get(self._url, params=params)
        except httpx.HTTPError as exc:
            raise UscallNetworkError(str(exc)) from exc
        if resp.status_code == 401 or resp.status_code == 403:
            raise UscallAuthError(f"http_{resp.status_code}")
        if resp.status_code >= 400:
            raise UscallProtocolError(f"http_{resp.status_code}")
        try:
            data = json.loads(resp.content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UscallProtocolError(f"invalid_json: {exc}") from exc
        if not isinstance(data, list):
            raise UscallProtocolError("payload_not_list")
        return data

    async def test(self) -> UscallTestResult:
        started = time.perf_counter()
        try:
            params = {"token": self.token, "tipo": "all"}
            async with httpx.AsyncClient(verify=self.verify_ssl, timeout=5.0) as client:
                resp = await client.get(self._url, params=params)
            latency_ms = int((time.perf_counter() - started) * 1000)
            return UscallTestResult(
                success=200 <= resp.status_code < 300,
                http_status=resp.status_code,
                latency_ms=latency_ms,
                error=None if resp.status_code < 400 else f"http_{resp.status_code}",
            )
        except httpx.HTTPError as exc:
            return UscallTestResult(
                success=False,
                http_status=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=type(exc).__name__,
            )
