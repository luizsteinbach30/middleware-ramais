"""GitHub Releases client used by the auto-updater.

Only depends on the REST API and reads:

* ``GET /repos/{owner}/{repo}/releases?per_page=20``

We pick the highest ``Version(tag.lstrip('v'))`` matching the current channel
and strictly greater than the running version. Pre-releases are filtered out
unless the channel is ``"beta"``.

O repositório de releases é **privado**: toda chamada precisa de um token
(fine-grained PAT somente-leitura) e o download de asset precisa usar a URL
de API do asset (``assets[].url``) com ``Accept: application/octet-stream`` —
o ``browser_download_url`` não funciona em repo privado nem autenticado.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from packaging.version import InvalidVersion, Version

UpdateMode = Literal["legacy", "standalone"]

_DOWNLOAD_HEADERS = {
    "Accept": "application/octet-stream",
    "X-GitHub-Api-Version": "2022-11-28",
}


@dataclass(slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    # URL de API do asset (api.github.com/.../releases/assets/{id}) — é a
    # única forma de baixar assets de repositório privado.
    api_url: str = ""


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
    exe: ReleaseAsset | None = None


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
            api_url=a.get("url", ""),
        )
        for a in item.get("assets", [])
    ]
    tarball = next((a for a in assets if a.name.startswith("app-") and a.name.endswith(".tar.gz")), None)
    sha = next((a for a in assets if a.name == "SHA256SUMS"), None)
    sha_sig = next((a for a in assets if a.name == "SHA256SUMS.asc"), None)
    exe = next(
        (a for a in assets if a.name.startswith("MiddlewareMonitor") and a.name.endswith(".exe")),
        None,
    )
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
        exe=exe,
    )


def _has_required_assets(release: Release, mode: UpdateMode) -> bool:
    """Uma release só é candidata se tiver os assets que o modo de install
    consegue aplicar: .exe (standalone/PyInstaller) ou tarball (legacy
    NSSM/systemd). SHA256SUMS é obrigatório nos dois modos."""
    if release.sha256sums is None:
        return False
    if mode == "standalone":
        return release.exe is not None
    return release.tarball is not None


async def download_asset(
    asset: ReleaseAsset,
    dest: Path,
    *,
    token: str | None = None,
    timeout: float = 300.0,
) -> None:
    """Baixa um asset de release para ``dest`` (streaming).

    Usa a URL de API do asset com ``Accept: application/octet-stream``;
    o GitHub responde 302 para o storage (S3/Azure) e o httpx **remove** o
    header ``Authorization`` no redirect cross-host — comportamento
    necessário, o storage rejeita requests com dupla autenticação."""
    url = asset.api_url or asset.download_url
    headers = dict(_DOWNLOAD_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(64 * 1024):
                    fh.write(chunk)


def download_asset_sync(
    url: str,
    dest: Path,
    *,
    token: str | None = None,
    timeout: float = 300.0,
) -> None:
    """Versão síncrona de :func:`download_asset` (usada pelo caminho
    standalone/desktop, que roda fora do event loop)."""
    headers = dict(_DOWNLOAD_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(64 * 1024):
                    fh.write(chunk)


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

    async def latest_for_channel(
        self,
        *,
        channel: str,
        current: Version,
        mode: UpdateMode = "legacy",
    ) -> Release | None:
        releases = await self.list_releases()
        candidates = [
            r
            for r in releases
            if (channel == "beta" or r.channel == "stable")
            and r.version > current
            and _has_required_assets(r, mode)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.version, reverse=True)
        return candidates[0]
