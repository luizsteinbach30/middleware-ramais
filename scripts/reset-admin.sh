#!/usr/bin/env bash
# reset-admin.sh — Middleware USCall Monitor: devolve o login admin / admin.
#
# Uso:  sudo bash reset-admin.sh [caminho/do/app.db]
#
# O painel não tem "esqueci a senha": o usuário é único e a troca exige a senha
# atual. Este script grava no banco um hash bcrypt novo (o de "admin"), marca a
# troca obrigatória no próximo login e zera o bloqueio de tentativas. Não importa
# o pacote do middleware — só o módulo sqlite3 da biblioteca padrão do Python que
# o instalador já deixou em /opt/middleware-monitor/python. O serviço pode ficar
# no ar: a senha vale no próximo login.
#
# Sem argumento, o banco vem de APP_DATA_DIR em /etc/middleware-monitor/env
# (padrão /var/lib/middleware-monitor/db/app.db). Executa como o usuário do
# serviço (mmonitor): arquivos app.db-wal/-shm criados pelo root travariam o
# serviço na próxima escrita.
#
# Variáveis opcionais: MM_PREFIX, MM_ENV_FILE, MM_USER, MM_PYTHON.
set -euo pipefail

PREFIX="${MM_PREFIX:-/opt/middleware-monitor}"
ENV_FILE="${MM_ENV_FILE:-/etc/middleware-monitor/env}"
SVC_USER="${MM_USER:-mmonitor}"
# bcrypt de "admin" (custo 12) — o mesmo formato que o painel grava.
HASH='$2b$12$mondvXbpzATNV2q.BLFFBuyh2G65fg8kL8vooEihB6MWiJq5G8y0i'

die() { echo "ERRO: $*" >&2; exit 2; }

# --- Python: runtime do .run, venv das versões <= 2.11, ou o do sistema -------
PY="${MM_PYTHON:-}"
if [[ -z "$PY" ]]; then
  for cand in "$PREFIX/python/bin/python3" "$PREFIX/venv/bin/python" "$(command -v python3 || true)"; do
    if [[ -n "$cand" && -x "$cand" ]]; then PY="$cand"; break; fi
  done
fi
[[ -n "$PY" ]] || die "python3 não encontrado (esperado em $PREFIX/python/bin/python3)"

# --- Banco --------------------------------------------------------------------
env_val() {  # env_val CHAVE → último valor da chave no env, sem aspas
  [[ -r "$ENV_FILE" ]] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed -e "s/^[\"']//" -e "s/[\"']\$//"
}
DB="${1:-}"
if [[ -z "$DB" ]]; then
  db_url="$(env_val APP_DB_URL)"
  if [[ -n "$db_url" ]]; then
    [[ "$db_url" == sqlite:///* ]] || die "APP_DB_URL não é SQLite ($db_url) — troque a senha direto nesse banco"
    DB="${db_url#sqlite:///}"
  else
    data_dir="$(env_val APP_DATA_DIR)"
    DB="${data_dir:-/var/lib/middleware-monitor}/db/app.db"
  fi
  if [[ ! -r "$ENV_FILE" && $EUID -ne 0 ]]; then
    echo "aviso: $ENV_FILE não é legível sem root — assumindo $DB" >&2
  fi
fi
[[ -f "$DB" ]] || die "banco não encontrado: $DB"

# --- Executar como o usuário do serviço ---------------------------------------
RUN=()
if [[ $EUID -eq 0 ]] && id -u "$SVC_USER" >/dev/null 2>&1; then
  RUN=(runuser -u "$SVC_USER" --)
elif [[ ! -w "$DB" ]]; then
  die "sem permissão de escrita em $DB — rode com sudo"
fi

echo "Banco:  $DB"
echo "Python: $PY"
${RUN[@]+"${RUN[@]}"} "$PY" - "$DB" "$HASH" <<'PY'
import sqlite3
import sys
from datetime import datetime, timezone

db_path, pw_hash = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db_path, timeout=5)
try:
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("BEGIN IMMEDIATE")
    cur = con.execute(
        "UPDATE users SET password_hash=?, must_change_password=1, "
        "failed_login_count=0, locked_until=NULL WHERE username='admin'",
        (pw_hash,),
    )
    if cur.rowcount == 0:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
        con.execute(
            "INSERT INTO users (username, password_hash, role, must_change_password, "
            "failed_login_count, created_at) VALUES ('admin', ?, 'admin', 1, 0, ?)",
            (pw_hash, now),
        )
        acao = "criado"
    else:
        acao = "redefinido"
    con.execute("DELETE FROM login_attempts WHERE success=0")
    con.commit()
finally:
    con.close()
print(f"Usuário admin {acao}.")
print("Login: admin / admin — a troca de senha é obrigatória no próximo acesso")
print("(mínimo 12 caracteres, com letras e números). Faça o login agora.")
PY
