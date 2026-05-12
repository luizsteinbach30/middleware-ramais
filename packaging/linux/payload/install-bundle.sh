#!/usr/bin/env bash
# Internal script invoked by middleware-monitor-installer-<ver>.run.
# Receives the extracted bundle directory as $1 and the target prefix as
# environment (or defaults).
set -euo pipefail

BUNDLE_DIR="${1:?bundle dir missing}"
APP_VERSION="${MM_VERSION:?MM_VERSION missing}"
PREFIX="${MM_PREFIX:-/opt/middleware-monitor}"
DATA="${MM_DATA:-/var/lib/middleware-monitor}"
ETC="${MM_ETC:-/etc/middleware-monitor}"
USER_SVC="${MM_USER:-mmonitor}"
LOG_FILE="${MM_LOG:-/tmp/mm-install.log}"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG_FILE"; }

if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

log "==> Detecting platform"
PY3=$(command -v python3 || true)
if [[ -z "$PY3" ]]; then
  log "Bundled wheels need Python 3.11+. Trying apt..."
  if command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip
  elif command -v dnf >/dev/null; then
    dnf install -y python3 python3-pip
  else
    echo "Could not locate python3 and no known package manager." >&2
    exit 1
  fi
fi

PY3="$(command -v python3)"
PY_VER="$("$PY3" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
log "Using python3 = $PY3 ($PY_VER)"
case "$PY_VER" in
  3.11|3.12|3.13) : ;;
  *) echo "Python 3.11+ required (got $PY_VER). Aborting." >&2; exit 1 ;;
esac

log "==> Creating service user and directories"
id -u "$USER_SVC" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_SVC"
mkdir -p "$PREFIX"/{app,venv} "$DATA"/{db,backups,tmp,logs} "$ETC"
chown -R "$USER_SVC:$USER_SVC" "$DATA"

log "==> Setting up venv"
if [[ ! -x "$PREFIX/venv/bin/python" ]]; then
  "$PY3" -m venv "$PREFIX/venv"
fi
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip

log "==> Installing bundled wheels (offline)"
"$PREFIX/venv/bin/pip" install --quiet --no-index --find-links "$BUNDLE_DIR/wheels" middleware-monitor

log "==> Staging app/$APP_VERSION"
rm -rf "$PREFIX/app/$APP_VERSION"
mkdir -p "$PREFIX/app/$APP_VERSION"
cp -r "$BUNDLE_DIR/app/." "$PREFIX/app/$APP_VERSION/"
ln -sfn "$PREFIX/app/$APP_VERSION" "$PREFIX/current"

log "==> Generating env file (first install only)"
if [[ ! -f "$ETC/env" ]]; then
  SECRET="$("$PREFIX/venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(64))')"
  cat > "$ETC/env" <<EOF
APP_HOST=0.0.0.0
APP_PORT=8080
APP_DATA_DIR=$DATA
APP_SECRET_KEY=$SECRET
APP_LOG_LEVEL=INFO
APP_LOG_JSON=true
APP_COOKIE_SECURE=false
APP_UPDATE_REPO=luizsteinbach30/middleware-ramais
APP_UPDATE_CHANNEL=stable
EOF
  chown root:"$USER_SVC" "$ETC/env"
  chmod 0640 "$ETC/env"
fi

log "==> Running migrations"
sudo -u "$USER_SVC" \
  env $(cat "$ETC/env" | xargs) \
  "$PREFIX/venv/bin/python" -m alembic -c "$PREFIX/current/alembic.ini" upgrade head 2>&1 | tee -a "$LOG_FILE"

log "==> Bootstrapping admin/admin (forced rotation on first login)"
sudo -u "$USER_SVC" \
  env $(cat "$ETC/env" | xargs) \
  "$PREFIX/venv/bin/python" "$PREFIX/current/scripts/bootstrap_admin.py" 2>&1 | tee -a "$LOG_FILE"

log "==> Installing systemd unit"
install -m 0644 "$BUNDLE_DIR/systemd/middleware-monitor.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now middleware-monitor.service

log "==> Installing CLI helper (/usr/local/bin/middleware-monitor-ctl)"
install -m 0755 "$BUNDLE_DIR/middleware-monitor-ctl" /usr/local/bin/middleware-monitor-ctl

log "==> Installing desktop shortcut (/usr/share/applications)"
mkdir -p /usr/share/applications
install -m 0644 "$BUNDLE_DIR/middleware-monitor.desktop" /usr/share/applications/middleware-monitor.desktop || true

log "==> Waiting for the service to respond"
for _ in $(seq 1 30); do
  if curl -fsS -m 2 http://127.0.0.1:8080/api/system/healthz >/dev/null 2>&1; then
    log "Service is UP at http://$(hostname -I | awk '{print $1}'):8080/"
    log "Login com:  admin / admin  — você será obrigado a trocar a senha."
    exit 0
  fi
  sleep 1
done
log "Service didn't come up in 30s. Check: journalctl -u middleware-monitor -n 100"
exit 1
