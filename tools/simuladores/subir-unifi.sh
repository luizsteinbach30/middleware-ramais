#!/usr/bin/env sh
# Sobe o simulador de gravador UniFi Protect na porta 18083 (só laboratório).
# Uso: ./subir-unifi.sh [porta] [usuario] [senha]
# Precisa de Python 3 (só a biblioteca padrão). Ctrl+C para parar.
set -e
PASTA="$(cd "$(dirname "$0")" && pwd)"
PORTA="${1:-18083}"
USUARIO="${2:-admin}"
SENHA="${3:-simulador123}"
# O primeiro Python que roda de verdade (no Windows, "python3" pode ser o atalho da loja).
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Python 3 nao encontrado no PATH." >&2; exit 1; }
exec "$PY" "$PASTA/unifi.py" --porta "$PORTA" --usuario "$USUARIO" --senha "$SENHA"
