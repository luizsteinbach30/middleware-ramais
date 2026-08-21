# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the single-file Middleware USCall Monitor desktop app.
# Build with:  pyinstaller --noconfirm packaging/windows/exe/MiddlewareMonitor.spec
#
# Output:  dist/MiddlewareMonitor.exe   (≈ 30-40 MB, fully self-contained)

import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).parent.parent.parent.resolve()  # type: ignore[name-defined]

# ---- discover bundled resources -------------------------------------------

datas = []

# Application sources: only what we need at runtime, no tests, no docs.
for src_rel in [
    "src/middleware_monitor/web/templates",
    "src/middleware_monitor/web/static",
    "src/middleware_monitor/core/migrations",
]:
    src = PROJECT_ROOT / src_rel
    if not src.exists():
        continue
    target_rel = src_rel.removeprefix("src/")
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in path.parts:
            continue
        rel_inside = path.relative_to(src)
        bundle_dir = str(Path(target_rel) / rel_inside.parent)
        datas.append((str(path), bundle_dir))

# Alembic config file — desktop.py looks for it at sys._MEIPASS/alembic.ini.
alembic_ini = PROJECT_ROOT / "alembic.ini"
if alembic_ini.exists():
    datas.append((str(alembic_ini), "."))

# Vendor config templates. These are read at runtime via
# ``Path(__file__).parent / "<vendor>_template.<ext>"`` by the extension
# configurator vendors. PyInstaller only pulls in .py modules, so without
# this every config render (compute hash/status, save planilha, apply) blows
# up with FileNotFoundError -> HTTP 500. Bundle them next to their package.
#
# The glob used to be "*.xml" only, which silently left out
# ``yealink_template.cfg`` (Yealink ships key=value, not XML) -- every Yealink
# environment broke in the packaged .exe while working fine from source. Match
# on the template suffix instead of guessing the format, so the next vendor
# that arrives with its own extension does not repeat this.
_VENDORS_REL = "src/middleware_monitor/integrations/extension_configurator/vendors"
vendors_dir = PROJECT_ROOT / _VENDORS_REL
for tpl in sorted(vendors_dir.glob("*_template.*")):
    datas.append((str(tpl), _VENDORS_REL.removeprefix("src/")))

# Base IANA de fusos. `tzdata` e um pacote SO DE DADOS (tzdata/zoneinfo/**):
# declarar hiddenimport nao traz os arquivos, e sem eles `ZoneInfo(...)` levanta
# ZoneInfoNotFoundError no Windows -- todo telefone cairia no fuso de fallback
# em silencio.
try:
    from PyInstaller.utils.hooks import collect_data_files

    datas += collect_data_files("tzdata")
except Exception:  # pragma: no cover - build sem tzdata instalado
    pass

# ---- hidden imports -------------------------------------------------------

# PyInstaller can't see imports done by APScheduler / SQLAlchemy / FastAPI
# through string lookups. List them explicitly so the bundle contains them.
hiddenimports = [
    # Fuso do servidor (hora dos telefones)
    "tzlocal",
    "tzdata",
    # uvicorn workers + protocols
    "uvicorn.workers",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    # APScheduler
    "apscheduler.schedulers.asyncio",
    "apscheduler.executors.pool",
    "apscheduler.executors.asyncio",
    "apscheduler.triggers.interval",
    "apscheduler.triggers.cron",
    "apscheduler.jobstores.memory",
    # Cliente MQTT (paho) — Python puro, mas o backend de websocket e os
    # submodulos sao resolvidos em runtime.
    "paho.mqtt",
    "paho.mqtt.client",
    "paho.mqtt.packettypes",
    "paho.mqtt.reasoncodes",
    "paho.mqtt.properties",
    "paho.mqtt.subscribeoptions",
    # SQLAlchemy dialects
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    # bcrypt backend + cryptography wheels
    "bcrypt",
    "cryptography.hazmat.backends.openssl",
    "cryptography.hazmat.primitives.kdf.hkdf",
    "cryptography.fernet",
    # Email validators / pydantic optional
    "pydantic.fields",
    "pydantic.networks",
    "pydantic_settings",
    # Our own packages (PyInstaller will follow imports, but be explicit)
    "middleware_monitor",
    "middleware_monitor.app",
    "middleware_monitor.desktop",
    "middleware_monitor.api.auth",
    "middleware_monitor.api.config",
    "middleware_monitor.api.collections",
    "middleware_monitor.api.dashboard",
    "middleware_monitor.api.devices",
    "middleware_monitor.api.logs",
    "middleware_monitor.api.mqtt",
    "middleware_monitor.api.system",
    "middleware_monitor.api.webhooks",
    "middleware_monitor.web.pages",
    "middleware_monitor.jobs.collect_extensions",
    "middleware_monitor.jobs.monitor_devices",
    "middleware_monitor.jobs.retention",
    "middleware_monitor.updater.service",
    "middleware_monitor.updater.client",
    "middleware_monitor.updater.checksums",
    "middleware_monitor.updater.installer",
    "middleware_monitor.updater.standalone",
    "middleware_monitor._embedded",
    "middleware_monitor.integrations.network.windows",
    "middleware_monitor.integrations.network.linux",
    "middleware_monitor.integrations.network.factory",
    "middleware_monitor.integrations.network.base",
    "middleware_monitor.integrations.uscall_client",
    "middleware_monitor.integrations.mqtt_client",
    "middleware_monitor.domain.mqtt.address",
    "middleware_monitor.domain.mqtt.coverage",
    "middleware_monitor.domain.mqtt.discovery",
    "middleware_monitor.domain.mqtt.parser",
    "middleware_monitor.domain.mqtt.repository",
    "middleware_monitor.domain.mqtt.schemas",
    "middleware_monitor.domain.mqtt.service",
    "middleware_monitor.domain.auth.service",
    "middleware_monitor.domain.config.repository",
    "middleware_monitor.domain.config.schemas",
    "middleware_monitor.domain.devices.repository",
    "middleware_monitor.domain.devices.schemas",
    "middleware_monitor.domain.collections.repository",
    "middleware_monitor.domain.webhooks.sender",
    "middleware_monitor.core.security",
    "middleware_monitor.core.crypto",
    "middleware_monitor.core.scheduler",
    "middleware_monitor.core.logging",
    "middleware_monitor.core.db",
    "middleware_monitor.core.models",
    # Alembic dynamic imports
    "alembic.runtime.migration",
    "alembic.runtime.environment",
    "alembic.config",
    "alembic.script",
    "alembic.command",
]

block_cipher = None

a = Analysis(
    [str(PROJECT_ROOT / "src" / "middleware_monitor" / "desktop.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter.test",
        "pytest",
        "respx",
        "pytest_asyncio",
        "freezegun",
        "ruff",
        "mypy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MiddlewareMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # GUI mode, no command-line window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,               # leave default; can be set later when we have an .ico
    uac_admin=False,         # runs as the current user — no UAC prompt
    version=None,
)
