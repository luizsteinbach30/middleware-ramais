# Instalação (referência avançada)

Para o passo-a-passo do dia-a-dia, **use [docs/MANUAL.md](MANUAL.md)** — este documento é referência para o que não está lá.

## Linux em uma linha

```bash
curl -fsSL https://github.com/luizsteinbach30/middleware-ramais/releases/latest/download/install.sh | sudo bash
```

O `install.sh` baixa da release o `middleware-monitor-installer-X.Y.Z.run` e o `SHA256SUMS`, confere o hash e executa o `.run`. **O mesmo comando atualiza** uma instalação existente: preserva `/etc/middleware-monitor/env` e `/var/lib/middleware-monitor`, faz backup do banco antes de migrar e volta à versão anterior se a nova não responder em 60 s.

Variáveis aceitas na frente do `bash` (todas opcionais):

| Variável | Para que serve |
|---|---|
| `MM_VERSION=2.12.0` | Instalar/voltar para uma versão específica em vez da última |
| `MM_CHANNEL=beta` | Aceitar pré-releases (`-rc`, `-beta`) |
| `MM_TOKEN=...` | Token do GitHub — só se o repositório voltar a ser privado |
| `MM_PREFIX`, `MM_DATA`, `MM_ETC`, `MM_PORT` | Caminhos e porta, se os padrões não servirem |

Exemplo: `curl -fsSL … | sudo MM_VERSION=2.12.0 bash`

### O que o `.run` carrega e o que exige

- **CPython próprio** ([python-build-standalone](https://github.com/astral-sh/python-build-standalone) 3.11, x86_64, glibc ≥ 2.17) e **todas as wheels**, resolvidas por esse mesmo interpretador a partir da wheel do projeto. No servidor não é preciso `python3`, apt, PyPI nem internet para instalar.
- **Exige:** Linux x86_64 com systemd (Ubuntu 20.04/22.04/24.04, Debian 11+, RHEL/Rocky 8+). O `install.sh` precisa de `curl` e `sha256sum`.
- Roda como usuário de sistema `mmonitor`, serviço `middleware-monitor`, com `/opt` somente-leitura para o serviço (`ProtectSystem=strict`).

### Servidor sem internet

Em qualquer máquina com internet:

```bash
curl -fsSL https://github.com/luizsteinbach30/middleware-ramais/releases/latest/download/install.sh | bash -s -- --download-only
```

Copie o `middleware-monitor-installer-X.Y.Z.run` para o servidor e rode `sudo bash middleware-monitor-installer-X.Y.Z.run --accept`. Sem internet a máquina não se atualiza sozinha; repita o processo a cada versão.

## Como o Linux se atualiza

Três caminhos, todos executados pela unidade `middleware-monitor-update` (root) — o serviço em si nunca escreve em `/opt`:

1. **Botão "Atualizar agora"** (painel → Sistema → Atualizações). O serviço grava `/var/lib/middleware-monitor/update.request`; a unidade `middleware-monitor-update.path` aciona o instalador como root, que baixa a release, confere o SHA256, faz backup do banco, troca o runtime, migra e reinicia. É o `APP_UPDATE_MODE=systemd` do env (o padrão `auto` vira `systemd` quando `/etc/middleware-monitor/env` existe).
2. **Timer diário** (`middleware-monitor-update.timer`, 00:00 + até 30 min). Por padrão **só verifica e avisa** — o painel tem a regra de que agendamento nunca instala. Para instalar sozinho: `middleware-monitor-ctl auto-update on` (grava `APP_UPDATE_AUTO_INSTALL=true` no env).
3. **Na mão:** `middleware-monitor-ctl update`, ou `MM_VERSION=2.11.0 middleware-monitor-ctl update` para fixar/voltar uma versão.

Logs: `middleware-monitor-ctl update-logs` (journal da unidade) e `/var/lib/middleware-monitor/logs/install.log`. Backups pré-upgrade: `/var/lib/middleware-monitor/backups/pre-upgrade_<de>_to_<para>_<data>.db`.

## Construir os instaladores localmente

Os instaladores prontos vivem em [GitHub Releases](https://github.com/luizsteinbach30/middleware-ramais/releases). Para gerar a partir do código:

### Linux `.run`

```bash
# Máquina de build: bash, curl, tar, makeself (instalado via apt/dnf se faltar) e internet.
bash packaging/linux/build_installer.sh
# Saída: dist/middleware-monitor-installer-X.Y.Z.run
```

O build baixa o CPython embutido (pinado em `PBS_RELEASE`/`PBS_PYTHON` no script), gera a wheel do projeto, resolve as wheels das dependências **com o Python embutido** (pinos de `packaging/constraints-build.txt`) e, ainda na máquina de build, instala tudo num Python descartável com `--no-index` — se faltar dependência o build quebra ali, não no cliente.

Testar num contêiner limpo (sem systemd, por isso `MM_NO_SYSTEMD=1` e o serviço é iniciado à mão):

```bash
docker run --rm -v "$PWD/dist:/dist:ro" ubuntu:24.04 bash -c '
  MM_NO_SYSTEMD=1 bash /dist/middleware-monitor-installer-*.run --accept --quiet
  cd /opt/middleware-monitor/current
  runuser -u mmonitor -- env $(grep -E "^[A-Z_]+=" /etc/middleware-monitor/env | xargs) \
    /opt/middleware-monitor/python/bin/python3 -m middleware_monitor & sleep 4
  /opt/middleware-monitor/python/bin/python3 -c "import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8080/api/system/healthz\").read())"'
```

### Windows `.exe`

```powershell
# Em um Windows com Python 3.11+ (PyInstaller via packaging/windows/exe/build_exe.ps1).
.\packaging\windows\exe\build_exe.ps1
```

---

## Layout em disco depois da instalação

### Linux

```
/opt/middleware-monitor/
├── current → app/<versão>/    (symlink)
├── app/
│   └── 2.12.0/                 (código, scripts, docs, alembic.ini)
└── python/                     (CPython embutido + dependências instaladas;
                                 até a 2.11 era venv/ sobre o python3 do sistema)
/etc/middleware-monitor/
└── env                         (APP_SECRET_KEY, porta, modo de update, token)
/var/lib/middleware-monitor/
├── db/app.db                   (SQLite WAL)
├── backups/                    (inclui pre-upgrade_*.db)
├── tmp/
├── logs/install.log
└── update.request              (transitório: pedido do botão "Atualizar agora")
/etc/systemd/system/middleware-monitor.service
/etc/systemd/system/middleware-monitor-update.{service,timer,path}
/usr/local/bin/middleware-monitor-ctl
/usr/local/bin/middleware-monitor-update   (o mesmo install.sh da release)
```

### Windows

```
%LOCALAPPDATA%\MiddlewareMonitor\
├── db\app.db
├── backups\
├── tmp\
├── logs\
└── secret.key
```

O `.exe` é standalone: não há pasta de programa.

---

## Variáveis de ambiente

Todas vivem em `/etc/middleware-monitor/env` (Linux). No `.exe` desktop, o que existe é o `APP_DATA_DIR` fixado em `%LOCALAPPDATA%\MiddlewareMonitor`.

| Variável | Padrão | Para que serve |
|---|---|---|
| `APP_HOST` | `0.0.0.0` | Bind do servidor HTTP |
| `APP_PORT` | `8080` | Porta HTTP |
| `APP_DATA_DIR` | `/var/lib/middleware-monitor` | Onde ficam DB, backups, tmp, logs |
| `APP_SECRET_KEY` | gerado na instalação | Chave de derivação para criptografia de tokens + assinatura de sessão. **Não troque sem rotacionar tokens.** |
| `APP_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `APP_LOG_JSON` | `true` em prod, `false` em dev | Logs JSON estruturados |
| `APP_COOKIE_SECURE` | `false` | `true` quando atrás de HTTPS |
| `APP_UPDATE_REPO` | `luizsteinbach30/middleware-ramais` | Repositório GitHub onde o updater procura novas versões |
| `APP_UPDATE_CHANNEL` | `stable` | `stable` ou `beta` |
| `APP_UPDATE_MODE` | `auto` | Como "Atualizar agora" aplica a atualização fora do `.exe`: `auto` (systemd se o env do `.run` existir, senão `legacy`), `systemd`, `legacy` |
| `APP_UPDATE_AUTO_INSTALL` | `false` | Linux: o timer diário instala releases novas sozinho (`true`) ou só avisa (`false`). `middleware-monitor-ctl auto-update on\|off` |
| `APP_UPDATE_TOKEN` | *(vazio)* | Token de leitura das releases. O repositório é público desde 2026-09, então normalmente fica vazio; se ele voltar a ser privado, defina aqui (ver RUNBOOK §4.1) |

Após editar o arquivo, reinicie o serviço para aplicar.

---

## Atualização manual única para a v2.7.0

Instalações com versão **≤ 2.6.0** têm o auto-update quebrado (o updater não
autenticava no repositório, então privado) e **não conseguem se atualizar sozinhas**.
É preciso **uma** atualização manual:

- **Desktop (.exe):** baixar `MiddlewareMonitor-X.Y.Z.exe` da release, fechar
  o app e substituir o executável.
- **Linux:** rodar a linha de instalação acima (funciona sobre qualquer versão
  anterior; a pasta `venv/` antiga é removida).

---

## Modo desenvolvedor

Sem instalador, rodando direto do código (para hackear features):

```bash
git clone https://github.com/luizsteinbach30/middleware-ramais.git
cd middleware-ramais
python3.11 -m venv .venv
source .venv/bin/activate     # ou .venv\Scripts\activate no Windows
pip install -e ".[dev,metrics]"

# Configure
export APP_DATA_DIR=$(pwd)/_data
export APP_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(64))")

# Migrações (o alembic.ini resolve o caminho por %(here)s — funciona de qualquer cwd)
python -m alembic upgrade head

# Cria admin/admin
python scripts/bootstrap_admin.py

# Sobe o servidor
python -m middleware_monitor
# Acesse http://127.0.0.1:8080  · login: admin / admin
```

Testes:
```bash
pytest -q
```

---

## Migração de v1.0 para v2.0

Se você tinha a v1.0 (que usava arquivos JSON em `data/`), há um script que importa para o banco SQLite:

```bash
python scripts/migrate_from_v1.py /caminho/para/data-v1
```

Idempotente — pode rodar várias vezes. Migra `config.json`, `devices.json`, `webhook_logs.json` e `collections/extensions/*.json`. Tokens são re-cifrados em repouso.
