#!/usr/bin/env bash
# Script embutido no middleware-monitor-installer-<versão>.run — o makeself o
# executa a partir da pasta extraída. Instala do zero ou atualiza no lugar,
# preservando /etc/middleware-monitor/env e /var/lib/middleware-monitor.
#
# Não usa python3 do sistema, apt nem PyPI: o bundle traz o CPython e as wheels.
# Toda falha depois da troca do runtime volta ao estado anterior (rollback).
#
# Variáveis opcionais: MM_PREFIX, MM_DATA, MM_ETC, MM_USER, MM_PORT, MM_TOKEN,
# MM_NO_SYSTEMD=1 (instala sem registrar unidades — contêiner/teste).
set -Eeuo pipefail
# O makeself executa este script com umask 077: sem isto, /opt/middleware-monitor
# e /etc/middleware-monitor nasceriam 700 e o usuário do serviço não passaria
# nem do primeiro diretório ("Permission denied" no python3).
umask 022

BUNDLE_DIR="$(cd "${1:-.}" && pwd)"
PREFIX="${MM_PREFIX:-/opt/middleware-monitor}"
DATA="${MM_DATA:-/var/lib/middleware-monitor}"
ETC="${MM_ETC:-/etc/middleware-monitor}"
USER_SVC="${MM_USER:-mmonitor}"
PORT="${MM_PORT:-8080}"
SERVICE=middleware-monitor
UPDATE_UNIT=middleware-monitor-update

if [[ "$EUID" -ne 0 ]]; then echo "Rode como root (sudo)." >&2; exit 1; fi
for f in VERSION python/bin/python3 wheels app/alembic.ini systemd/$SERVICE.service; do
  [[ -e "$BUNDLE_DIR/$f" ]] || { echo "bundle incompleto: falta $f em $BUNDLE_DIR" >&2; exit 1; }
done
APP_VERSION="$(tr -d '[:space:]' < "$BUNDLE_DIR/VERSION")"
case "$(uname -m)" in
  x86_64) : ;;
  *) echo "Este instalador é para x86_64 (aqui: $(uname -m))." >&2; exit 1 ;;
esac

mkdir -p "$DATA/logs"
LOG_FILE="${MM_LOG:-$DATA/logs/install.log}"
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG_FILE"; }

HAS_SYSTEMD=0
if [[ "${MM_NO_SYSTEMD:-0}" != "1" && -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
  HAS_SYSTEMD=1
fi

log "==> Middleware USCall Monitor $APP_VERSION → $PREFIX (dados em $DATA)"

# ------------------------------------------------------------- 1. usuário e pastas
id -u "$USER_SVC" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_SVC"
mkdir -p "$PREFIX/app" "$DATA"/{db,backups,tmp,logs} "$ETC"
# Pastas de uma tentativa anterior podem ter vindo 700 (umask do makeself).
chmod 755 "$PREFIX" "$PREFIX/app" "$ETC"
chown -R "$USER_SVC:$USER_SVC" "$DATA"

# ------------------------------------------------------------- 2. o que já está instalado
installed_version() {
  local py
  for py in "$PREFIX/python/bin/python3" "$PREFIX/venv/bin/python"; do
    [[ -x "$py" ]] && "$py" -c 'from middleware_monitor.version import __version__; print(__version__)' 2>/dev/null && return 0
  done
  return 1
}
PREV_VERSION="$(installed_version || true)"
PREV_TARGET="$(readlink -f "$PREFIX/current" 2>/dev/null || true)"
WAS_ACTIVE=0
if [[ $HAS_SYSTEMD == 1 ]] && systemctl is-active --quiet "$SERVICE"; then
  WAS_ACTIVE=1
  log "==> Parando o serviço (instalado: ${PREV_VERSION:-?})"
  systemctl stop "$SERVICE"
fi

# ------------------------------------------------------------- 3. runtime novo em python.new
# Fica em $PREFIX (e não na pasta extraída em /tmp) porque /tmp pode estar
# montado noexec — e porque é daqui que o serviço vai rodar.
log "==> Runtime: CPython $(cat "$BUNDLE_DIR/PYTHON_VERSION" 2>/dev/null || echo embutido) + wheels do bundle (sem PyPI)"
rm -rf "$PREFIX/python.new"
cp -r "$BUNDLE_DIR/python" "$PREFIX/python.new"
chown -R root:root "$PREFIX/python.new"
chmod -R a+rX "$PREFIX/python.new"
NPY="$PREFIX/python.new/bin/python3"
WHL="$(ls "$BUNDLE_DIR"/wheels/middleware_monitor-*.whl | head -1)"
PIP_ROOT_USER_ACTION=ignore "$NPY" -m pip install --quiet --disable-pip-version-check --no-index \
  --find-links "$BUNDLE_DIR/wheels" "${WHL}[metrics]" 2>&1 | tee -a "$LOG_FILE"
# Só o pacote (não `.app`: importar a aplicação carrega Settings e cria ./data).
"$NPY" -c 'import middleware_monitor.version as v; print("    middleware-monitor", v.__version__)' | tee -a "$LOG_FILE"

# ------------------------------------------------------------- 4. backup do banco
BK=""
if [[ -f "$DATA/db/app.db" ]]; then
  BK="$DATA/backups/pre-upgrade_${PREV_VERSION:-x}_to_${APP_VERSION}_$(date +%Y%m%d-%H%M%S).db"
  log "==> Backup do banco: $BK"
  # API de backup do SQLite: cópia consistente mesmo com WAL pendente.
  "$NPY" -c 'import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close(); src.close()' "$DATA/db/app.db" "$BK"
  chown "$USER_SVC:$USER_SVC" "$BK"
fi

# ------------------------------------------------------------- 5. código versionado
log "==> Código em $PREFIX/app/$APP_VERSION"
rm -rf "$PREFIX/app/$APP_VERSION"
mkdir -p "$PREFIX/app/$APP_VERSION"
cp -r "$BUNDLE_DIR/app/." "$PREFIX/app/$APP_VERSION/"
chown -R root:root "$PREFIX/app/$APP_VERSION"
chmod -R a+rX "$PREFIX/app/$APP_VERSION"

# ------------------------------------------------------------- 6. env
if [[ ! -f "$ETC/env" ]]; then
  log "==> Gerando $ETC/env (primeira instalação)"
  SECRET="$("$NPY" -c 'import secrets;print(secrets.token_urlsafe(64))')"
  cat > "$ETC/env" <<EOF
APP_HOST=0.0.0.0
APP_PORT=$PORT
APP_DATA_DIR=$DATA
APP_SECRET_KEY=$SECRET
APP_LOG_LEVEL=INFO
APP_LOG_JSON=true
APP_COOKIE_SECURE=false
APP_UPDATE_REPO=luizsteinbach30/middleware-ramais
APP_UPDATE_CHANNEL=stable
# Como o botão "Atualizar agora" do painel aplica a atualização nesta máquina:
# systemd = pede à unidade middleware-monitor-update (root) para instalar a release.
APP_UPDATE_MODE=systemd
# true = o timer diário INSTALA a release nova sozinho; false = só avisa no painel.
# (middleware-monitor-ctl auto-update on|off)
APP_UPDATE_AUTO_INSTALL=false
EOF
fi
ensure_env() { grep -q "^$1=" "$ETC/env" || printf '%s=%s\n' "$1" "$2" >> "$ETC/env"; }
ensure_env APP_UPDATE_MODE systemd
ensure_env APP_UPDATE_AUTO_INSTALL false
if [[ -n "${MM_TOKEN:-}" ]]; then
  # Token informado na instalação vira o token do updater (precede o embutido
  # no build). Preenche valor vazio; não sobrescreve um já definido.
  if grep -q '^APP_UPDATE_TOKEN=' "$ETC/env"; then
    sed -i "s|^APP_UPDATE_TOKEN=$|APP_UPDATE_TOKEN=$MM_TOKEN|" "$ETC/env"
  else
    printf 'APP_UPDATE_TOKEN=%s\n' "$MM_TOKEN" >> "$ETC/env"
  fi
fi
chown root:"$USER_SVC" "$ETC/env"
chmod 0640 "$ETC/env"
PORT_EFF="$(sed -n 's/^APP_PORT=//p' "$ETC/env" | tail -1)"
PORT_EFF="${PORT_EFF:-$PORT}"

# ------------------------------------------------------------- 7. troca: a partir daqui há rollback
ROLLED=0
rollback() {
  [[ $ROLLED == 1 ]] && return 0
  ROLLED=1
  log "!! Falha — voltando para ${PREV_VERSION:-a instalação anterior}"
  if [[ -d "$PREFIX/python.prev" ]]; then
    rm -rf "$PREFIX/python"
    mv "$PREFIX/python.prev" "$PREFIX/python"
  fi
  if [[ -n "$PREV_TARGET" && -d "$PREV_TARGET" ]]; then
    ln -sfn "$PREV_TARGET" "$PREFIX/current"
  fi
  if [[ $HAS_SYSTEMD == 1 && $WAS_ACTIVE == 1 ]]; then
    systemctl daemon-reload || true
    systemctl start "$SERVICE" || true
  fi
  [[ -n "$BK" ]] && log "   Banco de antes da tentativa: $BK"
  log "   Log completo: $LOG_FILE"
}
die() { log "ERRO: $*"; rollback; exit 1; }
# Só no shell principal: os subshells dos pipelines herdam o trap (set -E) e
# imprimiriam o rollback duas vezes.
trap '[[ $BASH_SUBSHELL == 0 ]] && rollback' ERR

if [[ -d "$PREFIX/python" ]]; then
  rm -rf "$PREFIX/python.prev"
  mv "$PREFIX/python" "$PREFIX/python.prev"
fi
mv "$PREFIX/python.new" "$PREFIX/python"
PY="$PREFIX/python/bin/python3"
ln -sfn "$PREFIX/app/$APP_VERSION" "$PREFIX/current"

# Variáveis do env como argumentos (não `source`: nada aqui deve vazar
# para o shell). runuser é do util-linux — existe até em contêiner mínimo.
mapfile -t ENV_ARGS < <(grep -E '^[A-Z_]+=' "$ETC/env")
run_as_svc() { runuser -u "$USER_SVC" -- env "${ENV_ARGS[@]}" "$@"; }

# ------------------------------------------------------------- 8. migrations e admin
# cwd = current: o alembic.ini resolve as migrations por %(here)s, mas o cwd
# certo garante o mesmo para versões antigas do ini.
log "==> Migrations"
( cd "$PREFIX/current" && run_as_svc "$PY" -m alembic -c alembic.ini upgrade head ) 2>&1 | tee -a "$LOG_FILE"

log "==> Usuário admin (só cria se não existir)"
( cd "$PREFIX/current" && run_as_svc "$PY" scripts/bootstrap_admin.py ) 2>&1 | tee -a "$LOG_FILE"

# ------------------------------------------------------------- 9. CLI, atualizador, atalho
install -m 0755 "$BUNDLE_DIR/middleware-monitor-ctl" /usr/local/bin/middleware-monitor-ctl
install -m 0755 "$BUNDLE_DIR/middleware-monitor-update" /usr/local/bin/middleware-monitor-update
mkdir -p /usr/share/applications
install -m 0644 "$BUNDLE_DIR/middleware-monitor.desktop" /usr/share/applications/middleware-monitor.desktop || true
rm -f "$DATA/update.request"

# ------------------------------------------------------------- 10. systemd e saúde
healthz_ok() {
  "$PY" -c 'import sys, urllib.request
urllib.request.urlopen("http://127.0.0.1:%s/api/system/healthz" % sys.argv[1], timeout=2).read()' "$PORT_EFF" >/dev/null 2>&1
}
if [[ $HAS_SYSTEMD == 1 ]]; then
  log "==> Unidades systemd (serviço, timer diário e gatilho do painel)"
  for u in "$SERVICE.service" "$UPDATE_UNIT.service" "$UPDATE_UNIT.timer" "$UPDATE_UNIT.path"; do
    sed -e "s|/opt/middleware-monitor|$PREFIX|g" \
        -e "s|/var/lib/middleware-monitor|$DATA|g" \
        -e "s|/etc/middleware-monitor|$ETC|g" \
        -e "s|^User=mmonitor|User=$USER_SVC|" \
        -e "s|^Group=mmonitor|Group=$USER_SVC|" \
        "$BUNDLE_DIR/systemd/$u" > "/etc/systemd/system/$u"
    chmod 0644 "/etc/systemd/system/$u"
  done
  systemctl daemon-reload
  systemctl enable "$SERVICE" "$UPDATE_UNIT.timer" "$UPDATE_UNIT.path" >/dev/null 2>&1
  systemctl restart "$SERVICE"
  systemctl start "$UPDATE_UNIT.timer" "$UPDATE_UNIT.path" || true

  log "==> Aguardando o serviço responder na porta $PORT_EFF"
  ok=0
  for _ in $(seq 1 60); do
    if healthz_ok; then ok=1; break; fi
    sleep 1
  done
  [[ $ok == 1 ]] || die "serviço não respondeu em 60 s — veja: journalctl -u $SERVICE -n 100"
fi

# ------------------------------------------------------------- 11. sucesso
trap - ERR
rm -rf "$PREFIX/python.prev" "$PREFIX/venv"   # venv: layout de ≤ 2.11
IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
log "==> Instalado: Middleware USCall Monitor $APP_VERSION${PREV_VERSION:+ (antes: $PREV_VERSION)}"
if [[ $HAS_SYSTEMD == 1 ]]; then
  log "    Painel:     http://${IP:-<ip>}:$PORT_EFF/"
  if [[ -z "$PREV_VERSION" ]]; then
    log "    Login:      admin / admin — a troca de senha é obrigatória no primeiro acesso"
  fi
  log "    Comandos:   middleware-monitor-ctl status|logs|restart|update|auto-update on"
else
  log "    Sem systemd aqui: para rodar à mão,"
  log "    cd $PREFIX/current && runuser -u $USER_SVC -- env \$(grep -E '^[A-Z_]+=' $ETC/env | xargs) $PY -m middleware_monitor"
fi
