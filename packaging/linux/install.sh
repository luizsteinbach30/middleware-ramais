#!/usr/bin/env bash
# Linux installer for Middleware USCall Monitor.
# Idempotent: re-run to install/upgrade. Pass --version vX.Y.Z to pin a release.
set -euo pipefail

REPO="${APP_UPDATE_REPO:-org/middleware-monitor}"
VERSION="${1:-}"
PREFIX="${PREFIX:-/opt/middleware-monitor}"
DATA="${APP_DATA_DIR:-/var/lib/middleware-monitor}"
ETC="${ETC:-/etc/middleware-monitor}"
SERVICE_USER="mmonitor"
UPDATER_USER="mmupdater"

if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root."; exit 1
fi

echo "==> Creating users + directories"
id -u "$SERVICE_USER" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
id -u "$UPDATER_USER" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$UPDATER_USER"

mkdir -p "$PREFIX"/{app,venv} "$DATA"/{db,backups,tmp} "$ETC"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA"

if [[ ! -x "$PREFIX/venv/bin/python" ]]; then
  echo "==> Creating venv"
  python3 -m venv "$PREFIX/venv"
fi

if [[ -z "$VERSION" ]]; then
  VERSION=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | grep '"tag_name"' | head -1 | cut -d'"' -f4)
fi
TAG="${VERSION#v}"
TARBALL="app-v${TAG}.tar.gz"

echo "==> Downloading ${TARBALL}"
TMP=$(mktemp -d)
curl -fsSL "https://github.com/$REPO/releases/download/$VERSION/$TARBALL" -o "$TMP/$TARBALL"
curl -fsSL "https://github.com/$REPO/releases/download/$VERSION/SHA256SUMS" -o "$TMP/SHA256SUMS"

echo "==> Verifying checksum"
( cd "$TMP" && sha256sum --check --ignore-missing SHA256SUMS )

echo "==> Extracting"
mkdir -p "$PREFIX/app/$TAG"
tar -xzf "$TMP/$TARBALL" -C "$PREFIX/app/$TAG" --strip-components=1

echo "==> Installing dependencies"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
if [[ -f "$PREFIX/app/$TAG/requirements.lock" ]]; then
  "$PREFIX/venv/bin/pip" install -q -r "$PREFIX/app/$TAG/requirements.lock" || \
    "$PREFIX/venv/bin/pip" install -q -e "$PREFIX/app/$TAG"
else
  "$PREFIX/venv/bin/pip" install -q -e "$PREFIX/app/$TAG"
fi

echo "==> Switching 'current'"
ln -sfn "$PREFIX/app/$TAG" "$PREFIX/current"
rm -rf "$TMP"

echo "==> Installing systemd units"
install -m 0644 "$PREFIX/current/packaging/linux/middleware-monitor.service" /etc/systemd/system/
install -m 0644 "$PREFIX/current/packaging/linux/middleware-monitor-updater.service" /etc/systemd/system/
install -m 0644 "$PREFIX/current/packaging/linux/middleware-monitor-updater.timer" /etc/systemd/system/

if [[ ! -f "$ETC/env" ]]; then
  echo "==> Generating $ETC/env"
  SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(64))")
  cat > "$ETC/env" <<EOF
APP_HOST=0.0.0.0
APP_PORT=8080
APP_DATA_DIR=$DATA
APP_SECRET_KEY=$SECRET
APP_LOG_LEVEL=INFO
APP_LOG_JSON=true
APP_COOKIE_SECURE=false
APP_UPDATE_REPO=$REPO
APP_UPDATE_CHANNEL=stable
APP_UPDATE_CHECK_MINUTES=60
EOF
  chown root:"$SERVICE_USER" "$ETC/env"
  chmod 0640 "$ETC/env"
fi

echo "==> Migrating DB"
sudo -u "$SERVICE_USER" "$PREFIX/venv/bin/python" -m alembic -c "$PREFIX/current/alembic.ini" upgrade head

echo "==> Bootstrapping admin (if first install)"
sudo -u "$SERVICE_USER" "$PREFIX/venv/bin/python" "$PREFIX/current/scripts/bootstrap_admin.py" || true

echo "==> Enabling services"
systemctl daemon-reload
systemctl enable --now middleware-monitor.service
systemctl enable --now middleware-monitor-updater.timer

echo
echo "Middleware USCall Monitor v${TAG} installed."
echo "Open: http://$(hostname -f):8080/"
