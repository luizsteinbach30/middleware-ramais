"""GithubReleasesClient — parse de assets, auth e validação por modo.

O repo de releases é privado: o client precisa mandar ``Authorization`` e
capturar a URL de API de cada asset (``assets[].url``) — o
``browser_download_url`` não funciona em repo privado.
"""

from __future__ import annotations

import httpx
import respx
from packaging.version import Version

from middleware_monitor.updater.client import (
    GithubReleasesClient,
    _has_required_assets,
    _parse_release,
    download_asset,
)

_RELEASES_URL = "https://api.github.com/repos/o/r/releases?per_page=20"


def _asset(name: str, aid: int = 1, size: int = 100) -> dict:
    return {
        "name": name,
        "size": size,
        "url": f"https://api.github.com/repos/o/r/releases/assets/{aid}",
        "browser_download_url": f"https://github.com/o/r/releases/download/tag/{name}",
    }


def _release(tag: str, assets: list[dict], *, prerelease: bool = False) -> dict:
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "published_at": "2026-06-03T12:00:00Z",
        "body": "notes",
        "assets": assets,
    }


_FULL_ASSETS = [
    _asset("app-v9.9.9.tar.gz", aid=1),
    _asset("MiddlewareMonitor-9.9.9.exe", aid=2),
    _asset("SHA256SUMS", aid=3),
]


def test_parse_release_captura_api_url_e_exe() -> None:
    r = _parse_release(_release("v9.9.9", _FULL_ASSETS))
    assert r is not None
    assert r.tarball is not None
    assert r.tarball.api_url == "https://api.github.com/repos/o/r/releases/assets/1"
    assert r.exe is not None
    assert r.exe.name == "MiddlewareMonitor-9.9.9.exe"
    assert r.exe.api_url == "https://api.github.com/repos/o/r/releases/assets/2"
    assert r.sha256sums is not None


def test_validacao_por_modo() -> None:
    completo = _parse_release(_release("v9.9.9", _FULL_ASSETS))
    so_tarball = _parse_release(
        _release("v9.9.9", [_asset("app-v9.9.9.tar.gz"), _asset("SHA256SUMS", aid=3)])
    )
    so_exe = _parse_release(
        _release("v9.9.9", [_asset("MiddlewareMonitor-9.9.9.exe"), _asset("SHA256SUMS", aid=3)])
    )
    sem_sha = _parse_release(
        _release("v9.9.9", [_asset("app-v9.9.9.tar.gz"), _asset("MiddlewareMonitor-9.9.9.exe")])
    )
    assert completo is not None and so_tarball is not None
    assert so_exe is not None and sem_sha is not None

    assert _has_required_assets(completo, "legacy")
    assert _has_required_assets(completo, "standalone")
    assert _has_required_assets(so_tarball, "legacy")
    assert not _has_required_assets(so_tarball, "standalone")
    assert not _has_required_assets(so_exe, "legacy")
    assert _has_required_assets(so_exe, "standalone")
    # SHA256SUMS é obrigatório nos dois modos.
    assert not _has_required_assets(sem_sha, "legacy")
    assert not _has_required_assets(sem_sha, "standalone")


@respx.mock
async def test_latest_for_channel_filtra_por_modo() -> None:
    respx.get(_RELEASES_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                _release(
                    "v9.9.9",
                    [_asset("MiddlewareMonitor-9.9.9.exe"), _asset("SHA256SUMS", aid=3)],
                )
            ],
        )
    )
    client = GithubReleasesClient("o/r")
    legacy = await client.latest_for_channel(
        channel="stable", current=Version("1.0.0"), mode="legacy"
    )
    standalone = await client.latest_for_channel(
        channel="stable", current=Version("1.0.0"), mode="standalone"
    )
    assert legacy is None
    assert standalone is not None
    assert str(standalone.version) == "9.9.9"


@respx.mock
async def test_authorization_header_enviado_quando_ha_token() -> None:
    route = respx.get(_RELEASES_URL).mock(
        return_value=httpx.Response(200, json=[_release("v9.9.9", _FULL_ASSETS)])
    )
    client = GithubReleasesClient("o/r", token="ghp_test123")
    await client.list_releases()
    sent = route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer ghp_test123"
    assert sent.headers["X-GitHub-Api-Version"] == "2022-11-28"


@respx.mock
async def test_sem_token_sem_authorization() -> None:
    route = respx.get(_RELEASES_URL).mock(
        return_value=httpx.Response(200, json=[_release("v9.9.9", _FULL_ASSETS)])
    )
    client = GithubReleasesClient("o/r")
    await client.list_releases()
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
async def test_prerelease_so_no_canal_beta() -> None:
    respx.get(_RELEASES_URL).mock(
        return_value=httpx.Response(
            200, json=[_release("v9.9.9-rc1", _FULL_ASSETS, prerelease=True)]
        )
    )
    client = GithubReleasesClient("o/r")
    stable = await client.latest_for_channel(channel="stable", current=Version("1.0.0"))
    beta = await client.latest_for_channel(channel="beta", current=Version("1.0.0"))
    assert stable is None
    assert beta is not None


@respx.mock
async def test_download_asset_usa_api_url_com_octet_stream(tmp_path) -> None:
    api_url = "https://api.github.com/repos/o/r/releases/assets/2"
    storage = "https://objects.example.com/blob/abc"
    api_route = respx.get(api_url).mock(
        return_value=httpx.Response(302, headers={"Location": storage})
    )
    respx.get(storage).mock(return_value=httpx.Response(200, content=b"exe-bytes"))

    r = _parse_release(_release("v9.9.9", _FULL_ASSETS))
    assert r is not None and r.exe is not None
    dest = tmp_path / "MiddlewareMonitor-9.9.9.exe"
    await download_asset(r.exe, dest, token="ghp_test123")

    assert dest.read_bytes() == b"exe-bytes"
    sent = api_route.calls.last.request
    assert sent.headers["Accept"] == "application/octet-stream"
    assert sent.headers["Authorization"] == "Bearer ghp_test123"
