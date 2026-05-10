"""GitHub Releases client used by the auto-updater.

Only depends on the public REST API and reads:

* ``GET /repos/{owner}/{repo}/releases?per_page=20``

We pick the highest ``Version(tag.lstrip('v'))`` matching the current channel
and strictly greater than the running version. Pre-releases are filtered out
unless the channel is ``"beta"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version


@dataclass(slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int


@dataclass(slots=True)
class Release:
    tag: str
    version: Version
    channel: str
    published_at: str
    notes: str
    assets: list[ReleaseAsset]
    tarball: ReleaseAsset | None
    sha256sums: ReleaseAsset | None
    sha256sums_sig: ReleaseAsset | None


def _parse_release(item: dict[str, Any]) -> Release | None:
    tag = item.get("tag_name") or ""
    if not tag:
        return None
    try:
        version = Version(tag.lstrip("v"))
    except InvalidVersion:
        return None
    is_prerelease = bool(item.get("prerelease"))
    channel = "beta" if is_prerelease else "stable"
    assets = [
        ReleaseAsset(
            name=a.get("name", ""),
            download_url=a.get("browser_download_url", ""),
            size=int(a.get("size", 0)),
        )
        for a in item.get("assets", [])
    ]
    tarball = next((a for a in assets if a.name.startswith("app-") and a.name.endswith(".tar.gz")), None)
    sha = next((a for a in assets if a.name == "SHA256SUMS"), None)
    sha_sig = next((a for a in assets if a.name == "SHA256SUMS.asc"), None)
    return Release(
        tag=tag,
        version=version,
        channel=channel,
        published_at=item.get("published_at", ""),
        notes=item.get("body", "") or "",
        assets=assets,
        tarball=tarball,
        sha256sums=sha,
        sha256sums_sig=sha_sig,
    )


class GithubReleasesClient:
    def __init__(self, repo: str, *, token: str | None = None, timeout: float = 15.0) -> None:
        if "/" not in repo:
            raise ValueError("repo must be in 'owner/name' form")
        self.repo = repo
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def list_releases(self) -> list[Release]:
        url = f"https://api.github.com/repos/{self.repo}/releases?per_page=20"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self._headers())
        resp.raise_for_status()
        out: list[Release] = []
        for item in resp.json():
            r = _parse_release(item)
            if r is not None:
                out.append(r)
        return out

    async def latest_for_channel(self, *, channel: str, current: Version) -> Release | None:
        releases = await self.list_releases()
        candidates = [
            r
            for r in releases
            if (channel == "beta" or r.channel == "stable")
            and r.version > current
            and r.tarball is not None
            and r.sha256sums is not None
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.version, reverse=True)
        return candidates[0]
