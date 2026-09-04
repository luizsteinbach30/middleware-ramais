#!/usr/bin/env bash
# Constrói o instalador Linux auto-extraível (.run).
#
# Saída: dist/middleware-monitor-installer-<versão>.run
#
# O .run carrega TUDO que o servidor precisa: um CPython próprio
# (python-build-standalone, da astral-sh), as wheels de todas as dependências
# resolvidas por ESSE interpretador e o código da aplicação. No servidor de
# destino não é preciso python3 do sistema, apt, PyPI nem internet.
#
# Requisitos só na máquina de BUILD: bash, curl, tar, makeself (instalado via
# apt/dnf se faltar) e internet (GitHub + PyPI).
#
# Por que um Python embutido: a v2.11.0 empacotava wheels cp311 e dependia do
# python3 do sistema ser exatamente 3.11 — Ubuntu 22.04 (3.10) abortava e
# 24.04 (3.12) quebrava no pip. Com o interpretador dentro do bundle, wheels e
# runtime nascem casados e o instalador deixa de depender da distro.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.."; pwd)"
BUILD="$ROOT/build/linux"
CACHE="${PBS_CACHE:-$ROOT/build/cache}"
DIST="$ROOT/dist"

# Versão: a única fonte da verdade é version.py. VERSION no ambiente sobrepõe
# (é o que o release.yml faz depois de validar tag == código).
VERSION="${VERSION:-$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/src/middleware_monitor/version.py")}"
[[ -n "$VERSION" ]] || { echo "não achei __version__ em src/middleware_monitor/version.py" >&2; exit 1; }

# CPython embutido — pinado. Trocar de versão aqui é mudança deliberada de
# runtime: rode o build e o teste em contêiner (docs/INSTALACAO.md) antes.
PBS_RELEASE="${PBS_RELEASE:-20260901}"
PBS_PYTHON="${PBS_PYTHON:-3.11.16}"
PBS_TRIPLE="${PBS_TRIPLE:-x86_64-unknown-linux-gnu}"
PBS_FILE="cpython-${PBS_PYTHON}+${PBS_RELEASE}-${PBS_TRIPLE}-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILE}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "falta '$1' na máquina de build" >&2; exit 1; }; }
need curl; need tar; need sed

rm -rf "$BUILD"
mkdir -p "$BUILD/wheels" "$BUILD/app" "$BUILD/systemd" "$BUILD/tools" "$BUILD/verify" "$CACHE" "$DIST"

echo "==> CPython embutido: $PBS_FILE"
if [[ ! -f "$CACHE/$PBS_FILE" ]]; then
  curl -fsSL --retry 3 -o "$CACHE/$PBS_FILE.part" "$PBS_URL"
  mv "$CACHE/$PBS_FILE.part" "$CACHE/$PBS_FILE"
fi
# Três cópias do mesmo tarball: a que vai no bundle fica intocada; a de
# ferramentas recebe `build`; a de verificação prova que as wheels fecham.
tar -xzf "$CACHE/$PBS_FILE" -C "$BUILD"          # $BUILD/python  (vai no bundle)
tar -xzf "$CACHE/$PBS_FILE" -C "$BUILD/tools"    # $BUILD/tools/python
tar -xzf "$CACHE/$PBS_FILE" -C "$BUILD/verify"   # $BUILD/verify/python
PY="$BUILD/tools/python/bin/python3"
"$PY" -c 'import sys; print("    python", sys.version.split()[0])'

echo "==> Wheel do projeto"
"$PY" -m pip install --quiet --disable-pip-version-check build
"$PY" -m build --wheel --outdir "$BUILD/wheels" "$ROOT" >/dev/null
WHL="$(ls "$BUILD/wheels"/middleware_monitor-*.whl)"
echo "    $(basename "$WHL")"

echo "==> Wheels das dependências (resolvidas pelo Python embutido; pinos de packaging/constraints-build.txt)"
"$PY" -m pip wheel --quiet --disable-pip-version-check \
  --wheel-dir "$BUILD/wheels" \
  --constraint "$ROOT/packaging/constraints-build.txt" \
  --prefer-binary \
  "${WHL}[metrics]"
echo "    $(ls "$BUILD/wheels" | wc -l) wheels"

# Prova, ainda na máquina de build, de que o conjunto fecha sem PyPI. Se
# faltar dependência (foi o caso do passlib na 2.11.0), quebra aqui — não no
# servidor do cliente.
echo "==> Verificação offline das wheels"
"$BUILD/verify/python/bin/python3" -m pip install --quiet --disable-pip-version-check \
  --no-index --find-links "$BUILD/wheels" "${WHL}[metrics]"
"$BUILD/verify/python/bin/python3" -c 'import middleware_monitor.app, alembic, passlib, openpyxl, fpdf, paho.mqtt, prometheus_client; print("    ok")'
rm -rf "$BUILD/verify" "$BUILD/tools"

echo "==> Código, scripts e docs"
for item in src scripts docs alembic.ini pyproject.toml README.md CHANGELOG.md LICENSE; do
  cp -r "$ROOT/$item" "$BUILD/app/"
done
find "$BUILD/app" -name __pycache__ -type d -prune -exec rm -rf {} +

echo "==> systemd, CLI e instalador"
cp "$ROOT"/packaging/linux/middleware-monitor.service \
   "$ROOT"/packaging/linux/middleware-monitor-update.service \
   "$ROOT"/packaging/linux/middleware-monitor-update.timer \
   "$ROOT"/packaging/linux/middleware-monitor-update.path \
   "$BUILD/systemd/"
cp "$ROOT/packaging/linux/payload/install-bundle.sh" "$BUILD/install-bundle.sh"
cp "$ROOT/packaging/linux/payload/middleware-monitor-ctl" "$BUILD/middleware-monitor-ctl"
cp "$ROOT/packaging/linux/payload/middleware-monitor.desktop" "$BUILD/middleware-monitor.desktop"
cp "$ROOT/packaging/linux/install.sh" "$BUILD/middleware-monitor-update"
chmod +x "$BUILD/install-bundle.sh" "$BUILD/middleware-monitor-ctl" "$BUILD/middleware-monitor-update"
printf '%s\n' "$VERSION" > "$BUILD/VERSION"
printf '%s\n' "$PBS_PYTHON+$PBS_RELEASE" > "$BUILD/PYTHON_VERSION"

if ! command -v makeself >/dev/null; then
  echo "==> Instalando makeself"
  if command -v apt-get >/dev/null; then
    sudo apt-get update -y && sudo apt-get install -y makeself
  elif command -v dnf >/dev/null; then
    sudo dnf install -y makeself
  else
    echo "makeself não encontrado e sem gerenciador de pacotes conhecido; instale manualmente." >&2
    exit 1
  fi
fi

OUT="$DIST/middleware-monitor-installer-$VERSION.run"
echo "==> Empacotando $OUT"
# O script de partida é UM caminho, sem argumentos com espaço: o makeself 2.5
# cita o nome inteiro ("$script"), e "env X=1 bash ./x.sh ." virava um arquivo
# inexistente (linha 709 do .run da 2.11.0). A versão vai no arquivo VERSION.
# --tar-extra: os arquivos chegam como root no servidor, não com o uid do runner.
makeself --gzip --nox11 \
  --tar-extra "--owner=0 --group=0 --numeric-owner" \
  --license "$ROOT/LICENSE" \
  "$BUILD" "$OUT" \
  "Middleware USCall Monitor v$VERSION" \
  ./install-bundle.sh

echo
echo "Instalador pronto: $OUT ($(du -h "$OUT" | cut -f1))"
echo "No servidor:  sudo bash $(basename "$OUT") --accept"
