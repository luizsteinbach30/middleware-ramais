# Middleware USCall Monitor

Aplicação Python/FastAPI multiplataforma (Windows + Linux) que coleta status de
ramais SIP do **USCall**, monitora rede via ping/ARP e envia eventos para
sistemas externos via webhook. **Auto-update** via tags do GitHub Releases.

> Versão atual: **v2.0.0** — refatoração completa da v1.0 (JSON files) para
> SQLite + auth + scheduler único + auto-update.

## Stack

- Python 3.11+ · FastAPI · SQLAlchemy 2.0 · Alembic · SQLite (WAL)
- structlog · APScheduler · httpx · Jinja2 · Tailwind (CDN) · Chart.js inline
- bcrypt · itsdangerous · cryptography (Fernet) · pydantic-settings

## Documentação

| Doc | O que tem |
|---|---|
| [docs/REQUISITOS.md](docs/REQUISITOS.md) | RFs/RNFs, ADRs, modelo de dados, protocolo de update |
| [docs/TELAS.md](docs/TELAS.md) | Especificação de cada tela |
| [docs/design/DESIGN_SYSTEM.md](docs/design/DESIGN_SYSTEM.md) | Tokens visuais, componentes, partials |
| [docs/INSTALACAO.md](docs/INSTALACAO.md) | Como instalar em Windows/Linux |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Diagnóstico e mitigação de incidentes |
| [docs/ADRs/](docs/ADRs/) | Decisões arquiteturais |
| [.claude/agents/](.claude/agents/) | Equipe de subagentes especialistas |

## Quick start (dev)

```bash
git clone git@github.com:org/middleware-monitor.git
cd middleware-monitor
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,metrics]"
cp .env.example .env
# Edite .env e gere APP_SECRET_KEY:
python -c "import secrets;print(secrets.token_urlsafe(64))"
alembic upgrade head
python scripts/bootstrap_admin.py    # imprime senha temporária
python -m middleware_monitor          # http://localhost:8080
```

Login com a senha temporária. A primeira ação obrigatória é trocar a senha.

## Instalação em servidor cliente

### Linux (one-liner)
```bash
curl -fsSL https://github.com/<org>/middleware-monitor/releases/latest/download/install.sh | sudo bash
```

### Windows (PowerShell admin)
```powershell
iwr -useb https://github.com/<org>/middleware-monitor/releases/latest/download/install.ps1 | iex
```

Detalhes em [docs/INSTALACAO.md](docs/INSTALACAO.md).

## Comandos úteis

```bash
# rodar testes
pytest

# rodar app
python -m middleware_monitor

# migrações
alembic upgrade head
alembic downgrade -1

# importar dados v1.0 legados
python scripts/migrate_from_v1.py ./old_data
```

## Arquitetura

```
src/middleware_monitor/
├── app.py / __main__.py   FastAPI + uvicorn
├── settings.py            pydantic-settings (.env)
├── version.py             __version__ semver
├── core/                  db, models, security, scheduler, crypto, metrics
├── domain/                auth, config, devices, collections, webhooks
├── integrations/          uscall_client, network/{linux,windows}, factory
├── jobs/                  collect_extensions, monitor_devices, retention
├── api/                   routers JSON
├── web/                   pages.py + templates Jinja2 + static (Tailwind CDN)
└── updater/               GitHub releases client + installer + service
```

## Telas

10 telas implementadas, fiéis ao design de referência: Login, Dashboard, Devices,
Detalhe do device, Coletas, Webhook logs, Logs, Configuração, Atualizações,
Conta. Todas consomem somente os endpoints REST documentados em
[docs/TELAS.md](docs/TELAS.md).

## Auto-update

O serviço watcher (`updater/service.py`) chama o endpoint
`https://api.github.com/repos/<owner>/<repo>/releases` a cada
`APP_UPDATE_CHECK_MINUTES`. Quando há nova versão no canal configurado:

1. Baixa `app-vX.Y.Z.tar.gz` + `SHA256SUMS`.
2. Verifica SHA256.
3. Extrai em `app/<X.Y.Z>/`, instala dependências, roda `alembic upgrade head`.
4. Atualiza symlink/junction `current` → nova pasta.
5. Reinicia o serviço (NSSM no Windows, systemd no Linux).
6. Probe `/api/system/healthz` por 60s. Se falhar, **rollback automático**.

Toda tentativa fica auditada em `update_history`.

## Contribuindo

PRs precisam passar em `ruff` + `mypy` (warnings ok) + `pytest` (≥70% cobertura
nos módulos modificados). Ver `.github/workflows/ci.yml`.

## Licença

Proprietária. Ver `LICENSE`.
