"""find_exe_asset — aceita dicts crus da API do GitHub e ReleaseAsset."""

from __future__ import annotations

from middleware_monitor.updater.client import ReleaseAsset
from middleware_monitor.updater.standalone import find_exe_asset


def test_dict_prefere_api_url() -> None:
    assets = [
        {"name": "SHA256SUMS", "url": "https://api/sha"},
        {
            "name": "MiddlewareMonitor-2.7.0.exe",
            "url": "https://api.github.com/repos/o/r/releases/assets/2",
            "browser_download_url": "https://github.com/o/r/releases/download/v/f.exe",
        },
    ]
    found = find_exe_asset(assets)
    assert found is not None
    assert found["name"] == "MiddlewareMonitor-2.7.0.exe"
    assert found["url"] == "https://api.github.com/repos/o/r/releases/assets/2"


def test_dict_sem_api_url_usa_browser_url() -> None:
    assets = [
        {
            "name": "MiddlewareMonitor-2.7.0.exe",
            "browser_download_url": "https://github.com/o/r/releases/download/v/f.exe",
        },
    ]
    found = find_exe_asset(assets)
    assert found is not None
    assert found["url"] == "https://github.com/o/r/releases/download/v/f.exe"


def test_release_asset_prefere_api_url() -> None:
    assets = [
        ReleaseAsset(
            name="MiddlewareMonitor-2.7.0.exe",
            download_url="https://github.com/browser",
            size=1,
            api_url="https://api.github.com/repos/o/r/releases/assets/9",
        ),
    ]
    found = find_exe_asset(assets)
    assert found is not None
    assert found["url"] == "https://api.github.com/repos/o/r/releases/assets/9"


def test_release_asset_sem_api_url_usa_download_url() -> None:
    assets = [
        ReleaseAsset(
            name="MiddlewareMonitor-2.7.0.exe",
            download_url="https://github.com/browser",
            size=1,
        ),
    ]
    found = find_exe_asset(assets)
    assert found is not None
    assert found["url"] == "https://github.com/browser"


def test_sem_exe_retorna_none() -> None:
    assets = [
        {"name": "app-v2.7.0.tar.gz", "url": "https://api/1"},
        {"name": "SHA256SUMS", "url": "https://api/2"},
    ]
    assert find_exe_asset(assets) is None
    assert find_exe_asset([]) is None
