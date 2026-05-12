# Instalação (referência avançada)

Para o passo-a-passo do dia-a-dia, **use [docs/MANUAL.md](MANUAL.md)** — este documento é só referência para casos não cobertos lá.

## Construir os instaladores localmente

Os instaladores prontos vivem em [GitHub Releases](https://github.com/luizsteinbach30/middleware-ramais/releases). Se você precisar gerar a partir do código (ex.: ambiente sem internet para baixar do GitHub):

### Linux `.run`

```bash
# Em uma máquina de build com python3.11+, internet e makeself.
bash packaging/linux/build_installer.sh
# Saída: dist/middleware-monitor-installer-X.Y.Z.run
```

### Windows `.exe`

```powershell
# Em um Windows com Python 3.11+ e Inno Setup 6 instalado (iscc.exe no PATH).
.\packaging\windows\build_installer.ps1 -Version 2.0.0
# Saída: packaging\windows\inno\MiddlewareMonitorSetup-X.Y.Z.exe
```

Os scripts de build **baixam** o Python embeddable, as wheels e o NSSM **na máquina de build**, e empacotam tudo dentro do instalador. O servidor de destino não precisa de internet.

---

## Layout em disco depois da instalação

### Linux

```
/opt/middleware-monitor/
├── current → app/<versão>/    (symlink atualizado pelo auto-update)
├── app/
│   └── 2.0.0/
└── venv/                       (Python isolado)
/etc/middleware-monitor/
└── env                         (APP_SECRET_KEY + URLs + portas)
/var/lib/middleware-monitor/
├── db/app.db                   (SQLite WAL)
├── backups/
├── tmp/
└── logs/
/etc/systemd/system/middleware-monitor.service
```

### Windows

```
C:\Program Files\MiddlewareMonitor\
├── current → app\<versão>\     (junction NTFS)
├── app\
│   └── 2.0.0\
├── python\                     (Python embeddable)
├── wheels\                     (wheels offline)
├── bin\nssm.exe
└── scripts\
C:\ProgramData\MiddlewareMonitor\
├── db\app.db
├── backups\
├── tmp\
├── logs\app.log + app.err
└── env.cmd
```

---

## Variáveis de ambiente

Todas vivem em `env` (Linux) ou `env.cmd` (Windows):

| Variável | Padrão | Para que serve |
|---|---|---|
| `APP_HOST` | `0.0.0.0` | Bind do servidor HTTP |
| `APP_PORT` | `8080` | Porta HTTP |
| `APP_DATA_DIR` | `/var/lib/middleware-monitor` ou `C:\ProgramData\MiddlewareMonitor` | Onde fica DB, backups, tmp, logs |
| `APP_SECRET_KEY` | gerado na instalação | Chave de derivação para criptografia de tokens + assinatura de sessão. **Não troque sem rotacionar tokens.** |
| `APP_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `APP_LOG_JSON` | `true` em prod, `false` em dev | Logs JSON estruturados |
| `APP_COOKIE_SECURE` | `false` | `true` quando atrás de HTTPS |
| `APP_UPDATE_REPO` | `luizsteinbach30/middleware-ramais` | Repositório GitHub onde o updater procura novas versões |
| `APP_UPDATE_CHANNEL` | `stable` | `stable` ou `beta` |

Após editar o arquivo, reinicie o serviço para aplicar.

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

# Migrações
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
