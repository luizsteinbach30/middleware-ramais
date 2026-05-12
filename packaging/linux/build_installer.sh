#!/usr/bin/env bash
# Builds the self-extracting Linux installer (.run).
#
# Output: dist/middleware-monitor-installer-<version>.run
#
# Requirements on the build machine:
#   * Python 3.11+ to download wheels and build the package
#   * curl, tar, gzip, makeself (auto-installed if missing)
#
# After the build, the resulting .run is fully offline-installable on any
# Linux server with Python 3.11+ (or apt/dnf to install it).
set -euo pipefail

VERSION="${VERSION:-2.0.0}"
ROOT="$(cd "$(dirname "$0")/../.."; pwd)"
BUILD="$ROOT/build/linux"
DIST="$ROOT/dist"

rm -rf "$BUILD"
mkdir -p "$BUILD/wheels" "$BUILD/app" "$BUILD/systemd" "$DIST"

echo "==> Building wheel"
( cd "$ROOT" && python3 -m pip install --quiet --upgrade build wheel pip )
( cd "$ROOT" && python3 -m build --wheel --outdir "$BUILD/wheels" )

echo "==> Downloading dependency wheels"
python3 -m pip wheel --quiet --wheel-dir "$BUILD/wheels" \
  "fastapi>=0.110" "uvicorn[standard]>=0.27" "sqlalchemy>=2.0.25" "alembic>=1.13" \
  "pydantic>=2.5" "pydantic-settings>=2.1" "structlog>=24.1" "httpx>=0.27" \
  "jinja2>=3.1" "bcrypt>=4.1,<5" "itsdangerous>=2.1" "cryptography>=42.0" \
  "apscheduler>=3.10" "python-multipart>=0.0.7" "packaging>=23.0"

echo "==> Copying app + scripts"
cp -r "$ROOT/src" "$BUILD/app/src"
cp -r "$ROOT/scripts" "$BUILD/app/scripts"
cp -r "$ROOT/docs" "$BUILD/app/docs"
cp "$ROOT/alembic.ini" "$BUILD/app/"
cp "$ROOT/pyproject.toml" "$BUILD/app/"
cp "$ROOT/README.md" "$BUILD/app/"
cp "$ROOT/CHANGELOG.md" "$BUILD/app/"
cp "$ROOT/packaging/linux/middleware-monitor.service" "$BUILD/systemd/"
cp "$ROOT/packaging/linux/payload/install-bundle.sh" "$BUILD/install-bundle.sh"
chmod +x "$BUILD/install-bundle.sh"

if ! command -v makeself >/dev/null; then
  echo "==> Installing makeself"
  if command -v apt-get >/dev/null; then
    sudo apt-get update -y
    sudo apt-get install -y makeself
  elif command -v dnf >/dev/null; then
    sudo dnf install -y makeself
  else
    echo "makeself not found and no package manager known; install it manually." >&2
    exit 1
  fi
fi

OUT="$DIST/middleware-monitor-installer-$VERSION.run"
echo "==> Building $OUT"
makeself --gzip --notemp --nox11 \
  --license "$ROOT/LICENSE" \
  "$BUILD" "$OUT" \
  "Middleware USCall Monitor v$VERSION" \
  "env MM_VERSION=$VERSION bash ./install-bundle.sh ."

echo
echo "Installer ready: $OUT"
ls -lh "$OUT"
echo
echo "Para instalar no servidor cliente, copie e rode:"
echo "  sudo bash $(basename "$OUT")"
