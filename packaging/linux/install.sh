#!/usr/bin/env bash
# Middleware USCall Monitor — instalar ou atualizar no Linux com UMA linha:
#
#   curl -fsSL https://github.com/luizsteinbach30/middleware-ramais/releases/latest/download/install.sh | sudo bash
#
# Baixa da release (a mais nova, ou a de MM_VERSION) o instalador
# middleware-monitor-installer-<versão>.run e o SHA256SUMS, confere o hash e
# executa o .run — que traz Python próprio e todas as dependências. Depois da
# primeira instalação este mesmo script fica em
# /usr/local/bin/middleware-monitor-update, e é ele que o timer diário e o
# botão "Atualizar agora" do painel executam.
#
# Uso:  install.sh [--check | --if-newer | --download-only]
#   --check          versão instalada × disponível; não instala (não pede root)
#   --if-newer       instala só se a release for mais nova (é o que o timer roda;
#                    respeita APP_UPDATE_AUTO_INSTALL, salvo pedido do painel)
#   --download-only  só baixa o .run e o SHA256SUMS na pasta atual (para levar a
#                    um servidor sem internet e rodar `sudo bash x.run --accept`)
#
# Variáveis (todas opcionais):
#   MM_VERSION  versão a instalar: v2.12.0 ou 2.12.0 (padrão: última release estável)
#   MM_CHANNEL  stable (padrão) | beta — beta aceita pré-releases (-rc/-beta)
#   MM_REPO     owner/repo (padrão luizsteinbach30/middleware-ramais)
#   MM_TOKEN    token do GitHub — só é preciso se o repositório for privado
#   MM_PREFIX / MM_DATA / MM_ETC / MM_PORT — repassados ao instalador
set -euo pipefail

MODE=install
for a in "$@"; do
  case "$a" in
    --check) MODE=check ;;
    --if-newer) MODE=ifnewer ;;
    --download-only) MODE=download ;;
    -h|--help)
      cat <<'EOF'
Uso: middleware-monitor-update [--check | --if-newer | --download-only]
  (sem opção)      instala/atualiza para a última release (ou MM_VERSION)
  --check          versão instalada × disponível; não instala
  --if-newer       instala só se a release for mais nova (uso do timer)
  --download-only  só baixa o .run e o SHA256SUMS na pasta atual
Variáveis: MM_VERSION, MM_CHANNEL (stable|beta), MM_REPO, MM_TOKEN
EOF
      exit 0 ;;
    *) echo "opção desconhecida: $a" >&2; exit 2 ;;
  esac
done

log() { printf '[mm-update] %s\n' "$*"; }
die() { printf '[mm-update] ERRO: %s\n' "$*" >&2; exit 1; }

PREFIX="${MM_PREFIX:-/opt/middleware-monitor}"
ETC="${MM_ETC:-/etc/middleware-monitor}"

# Config do updater: MM_* > APP_* do ambiente > /etc/middleware-monitor/env.
# Só estas chaves são lidas do arquivo (nada de `source`).
F_REPO=""; F_CHANNEL=""; F_TOKEN=""; F_AUTO=""; F_DATA=""
if [[ -r "$ETC/env" ]]; then
  while IFS='=' read -r k v; do
    case "$k" in
      APP_UPDATE_REPO) F_REPO="$v" ;;
      APP_UPDATE_CHANNEL) F_CHANNEL="$v" ;;
      APP_UPDATE_TOKEN) F_TOKEN="$v" ;;
      APP_UPDATE_AUTO_INSTALL) F_AUTO="$v" ;;
      APP_DATA_DIR) F_DATA="$v" ;;
    esac
  done < <(grep -E '^APP_(UPDATE_|DATA_DIR=)' "$ETC/env" || true)
fi
DATA="${MM_DATA:-${APP_DATA_DIR:-${F_DATA:-/var/lib/middleware-monitor}}}"
REPO="${MM_REPO:-${APP_UPDATE_REPO:-${F_REPO:-luizsteinbach30/middleware-ramais}}}"
CHANNEL="${MM_CHANNEL:-${APP_UPDATE_CHANNEL:-${F_CHANNEL:-stable}}}"
TOKEN="${MM_TOKEN:-${APP_UPDATE_TOKEN:-$F_TOKEN}}"
AUTO_INSTALL="$(printf '%s' "${MM_AUTO_INSTALL:-${APP_UPDATE_AUTO_INSTALL:-$F_AUTO}}" | tr '[:upper:]' '[:lower:]')"
WANT="${MM_VERSION:-}"
WANT="${WANT#v}"

command -v curl >/dev/null 2>&1 || die "preciso de curl (Debian/Ubuntu: apt-get install -y curl)"
command -v sha256sum >/dev/null 2>&1 || die "preciso de sha256sum (coreutils)"

gh_curl() {
  # curl não repassa Authorization ao redirecionar para outro host (o storage
  # da release), então o header só chega à API — que é o que queremos.
  if [[ -n "$TOKEN" ]]; then
    curl -fsSL --retry 3 -H "Authorization: Bearer $TOKEN" "$@"
  else
    curl -fsSL --retry 3 "$@"
  fi
}

installed_version() {
  local py
  for py in "$PREFIX/python/bin/python3" "$PREFIX/venv/bin/python"; do
    [[ -x "$py" ]] && "$py" -c 'from middleware_monitor.version import __version__; print(__version__)' 2>/dev/null && return 0
  done
  return 1
}
CURRENT="$(installed_version || true)"

# ---------------------------------------------------------------- resolver a release
# Resultado: VERSION, RUN_NAME, RUN_URL, SUMS_URL (+ headers de download).
ACCEPT_OCTET=()
resolve_public_direct() {
  # Repositório público, canal stable: sem API — os links "latest/download"
  # e "download/v<versão>" do GitHub bastam.
  local base sums
  if [[ -n "$WANT" ]]; then
    base="https://github.com/$REPO/releases/download/v$WANT"
  else
    base="https://github.com/$REPO/releases/latest/download"
  fi
  sums="$(gh_curl "$base/SHA256SUMS")" || return 1
  RUN_NAME="$(printf '%s\n' "$sums" | grep -o 'middleware-monitor-installer-[0-9A-Za-z.+-]*\.run' | head -1)"
  [[ -n "$RUN_NAME" ]] || return 1
  VERSION="${RUN_NAME#middleware-monitor-installer-}"; VERSION="${VERSION%.run}"
  # Depois de saber a versão, baixa pelo link fixo dela (o "latest" pode mudar no meio).
  RUN_URL="https://github.com/$REPO/releases/download/v$VERSION/$RUN_NAME"
  SUMS_URL="https://github.com/$REPO/releases/download/v$VERSION/SHA256SUMS"
}
resolve_via_api() {
  # Repositório privado (token) ou canal beta: precisa da API e das URLs de
  # asset (api.github.com/.../releases/assets/<id>).
  local py json
  for py in "$PREFIX/python/bin/python3" python3; do
    command -v "$py" >/dev/null 2>&1 && break
    py=""
  done
  [[ -n "$py" ]] || die "para canal beta ou repositório privado preciso de python3 para ler a API do GitHub"
  json="$(gh_curl -H "Accept: application/vnd.github+json" "https://api.github.com/repos/$REPO/releases?per_page=30")" || return 1
  local out
  out="$(printf '%s' "$json" | "$py" -c '
import json, sys
want, channel = sys.argv[1], sys.argv[2]
for rel in json.load(sys.stdin):
    if rel.get("draft"):
        continue
    if channel != "beta" and rel.get("prerelease"):
        continue
    tag = rel.get("tag_name", "")
    if want and tag.lstrip("v") != want:
        continue
    assets = {a["name"]: a["url"] for a in rel.get("assets", [])}
    run = next((n for n in assets if n.startswith("middleware-monitor-installer-") and n.endswith(".run")), None)
    if run and "SHA256SUMS" in assets:
        print(tag.lstrip("v")); print(run); print(assets[run]); print(assets["SHA256SUMS"])
        break
' "$WANT" "$CHANNEL")" || return 1
  [[ -n "$out" ]] || return 1
  { read -r VERSION; read -r RUN_NAME; read -r RUN_URL; read -r SUMS_URL; } <<< "$out"
  ACCEPT_OCTET=(-H "Accept: application/octet-stream")
}
VERSION=""; RUN_NAME=""; RUN_URL=""; SUMS_URL=""
if [[ -z "$TOKEN" && "$CHANNEL" != "beta" ]]; then
  resolve_public_direct || resolve_via_api || die "não achei release ${WANT:+v$WANT }em $REPO (canal $CHANNEL)"
else
  resolve_via_api || die "não achei release ${WANT:+v$WANT }em $REPO (canal $CHANNEL)${TOKEN:+ — token válido?}"
fi

is_newer() {  # is_newer A B → A > B (ordem de versão)
  [[ "$1" != "$2" && "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" == "$1" ]]
}

if [[ $MODE == check ]]; then
  log "instalada: ${CURRENT:-nenhuma} · disponível ($CHANNEL): $VERSION"
  if [[ -z "$CURRENT" ]] || is_newer "$VERSION" "$CURRENT"; then exit 10; else exit 0; fi
fi

if [[ $MODE == ifnewer ]]; then
  REQUESTED=0
  if [[ -e "$DATA/update.request" ]]; then
    REQUESTED=1
    rm -f "$DATA/update.request"
    log "pedido do painel: atualizar agora"
  fi
  if [[ -n "$CURRENT" ]] && ! is_newer "$VERSION" "$CURRENT"; then
    log "instalada $CURRENT já é a mais nova ($CHANNEL: $VERSION) — nada a fazer"
    exit 0
  fi
  if [[ $REQUESTED == 0 && "$AUTO_INSTALL" != "true" && "$AUTO_INSTALL" != "1" ]]; then
    log "release $VERSION disponível (instalada: ${CURRENT:-nenhuma}); instalação automática desligada — ligue com: middleware-monitor-ctl auto-update on"
    exit 0
  fi
fi

# ---------------------------------------------------------------- baixar e conferir
TMP="$(mktemp -d "${TMPDIR:-/tmp}/mm-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
log "baixando $RUN_NAME (release v$VERSION de $REPO)"
gh_curl "${ACCEPT_OCTET[@]}" -o "$TMP/SHA256SUMS" "$SUMS_URL"
gh_curl "${ACCEPT_OCTET[@]}" -o "$TMP/$RUN_NAME" "$RUN_URL"
( cd "$TMP" && sha256sum -c --ignore-missing --quiet SHA256SUMS ) || die "SHA256 de $RUN_NAME não confere com o SHA256SUMS da release"
log "sha256 ok"

if [[ $MODE == download ]]; then
  mv "$TMP/$RUN_NAME" "$TMP/SHA256SUMS" .
  log "baixado em $(pwd): $RUN_NAME e SHA256SUMS — instale com: sudo bash $RUN_NAME --accept"
  exit 0
fi

[[ "$EUID" -eq 0 ]] || die "instalar exige root: rode com sudo"

# Uma atualização por vez (timer e gatilho do painel podem coincidir).
LOCK_DIR=/run/lock; [[ -d $LOCK_DIR && -w $LOCK_DIR ]] || LOCK_DIR=/tmp
exec 9>"$LOCK_DIR/middleware-monitor-update.lock"
flock -n 9 || die "outra instalação/atualização já está em andamento"

log "instalando v$VERSION${CURRENT:+ (instalada: $CURRENT)}"
export MM_TOKEN="$TOKEN" MM_PREFIX="$PREFIX" MM_DATA="$DATA" MM_ETC="$ETC"
bash "$TMP/$RUN_NAME" --accept --quiet --target "$TMP/bundle"
