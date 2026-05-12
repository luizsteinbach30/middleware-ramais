# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/) · SemVer.

## [2.0.2] — 2026-05-12

### Added
- **Painel de Controle nativo no Windows** (WinForms via PowerShell).
  Atalho no Menu Iniciar e na Área de Trabalho (opcional). Mostra status
  em tempo real e tem botões para Iniciar, Parar (Finalizar), Reiniciar,
  Abrir Painel web e Abrir Logs. Não exige PowerShell aberto pelo
  usuário — UAC é solicitado só nos botões de ação.
- **Atalhos no Menu Iniciar** (Windows): "Painel do Middleware",
  "Abrir Aplicação", "Pasta de Logs" e "Desinstalar".
- **CLI Linux `middleware-monitor-ctl`** instalada em `/usr/local/bin`.
  Comandos: `start`, `stop`, `restart`, `status`, `open`, `logs`,
  `install-log`. Funciona como atalho rápido sem precisar lembrar
  `systemctl`.
- **Atalho `.desktop` Linux** em `/usr/share/applications` (aparece no
  menu de aplicações em ambientes XDG/GNOME/KDE).

### Fixed
- O instalador anterior abria o navegador antes do serviço estar pronto
  em algumas máquinas. Agora abre primeiro o Painel de Controle, que
  mostra o status em tempo real e permite abrir o navegador quando
  o serviço estiver `Running`.

## [2.0.1] — 2026-05-12

### Added
- **Instalador Windows `.exe` self-contained** (Inno Setup + Python
  embeddable + wheels offline + NSSM). Não exige Python, NSSM ou internet
  no servidor de destino.
- **Instalador Linux `.run` self-extracting** (makeself) com wheels
  offline. Só exige `python3.11+` (instalado automaticamente via apt/dnf
  se faltar).
- Workflow `release.yml` agora gera **ambos os instaladores** + tarball
  fonte ao publicar uma tag `vX.Y.Z`.
- Job `update_check` migrado para **cron diário às 00:00 UTC**.
- Job `retention_daily` agora roda 00:30 UTC (era a cada 24h interval).

### Changed
- **Default admin agora é `admin` / `admin`** com troca obrigatória no
  primeiro login. Substitui a senha temporária aleatória da v2.0.0 que
  ficava no log do instalador.
- UI de configuração só envia campos que foram efetivamente alterados
  (dirty tracking), evitando rejeitar o save por validação de campos
  intactos.

### Fixed
- **Save de configuração falhava silenciosamente** quando um input
  numérico estivesse vazio (`+"" = 0` violava `ge=1`/`ge=10`). Agora
  inputs vazios viram `null` no payload e o toast exibe a mensagem real
  do backend (`ping_concurrency: Input should be greater than or equal
  to 1`, por exemplo).
- Botão `Salvar configuração` agora desabilita durante o request e
  re-habilita ao final, evitando duplo-click acidental.

## [2.0.0] — 2026-05-09

Reescrita completa da fundação. Compatibilidade do banco quebra; use
`scripts/migrate_from_v1.py` para importar dados da v1.0.

### Added
- Pacote Python `middleware_monitor` com layout `src/`.
- Persistência **SQLite (WAL) + Alembic** substituindo `data/*.json`.
- **Autenticação local** com bcrypt, sessões em DB, CSRF.
- **Auto-update** via GitHub Releases (canal `stable`/`beta`), com
  verificação SHA256 e rollback automático.
- Scheduler único com APScheduler (`AsyncIOScheduler`).
- Webhook sender com **retry/backoff** e auditoria por tentativa.
- 10 telas server-rendered (Tailwind via CDN) fiéis ao design system.
- Métricas Prometheus opt-in em `/api/system/metrics`.
- Healthchecks: `/api/system/healthz`, `/api/system/readyz`, banner global.
- Suite de testes (25 testes) cobrindo unit/integration/API.
- Workflows GitHub Actions para CI e Release.
- Instaladores `packaging/linux/install.sh` e `packaging/windows/install.ps1`.
- Documentação: REQUISITOS, TELAS, DESIGN_SYSTEM, INSTALACAO, RUNBOOK.
- 9 subagentes especializados em `.claude/agents/`.

### Changed
- Estrutura de diretórios: tudo migrado de `core/`, `services/`, `api/` (raiz)
  para `src/middleware_monitor/`.
- Configuração editável agora vive em `app_config` (DB) com cripto em repouso
  para tokens; `.env` é só infra (paths, porta, secret material).
- Endpoint USCall usa `verify=True` por padrão (toggle explícito).

### Fixed
- Race condition no logger JSON (B-06): logs vão para `system_logs` em transação.
- Retenção O(n²) de webhook_logs (B-07): job diário em batch.
- Pings sequenciais (B-11): `asyncio.gather` com `Semaphore`.
- Caminhos relativos dependentes do CWD (B-20): tudo via `APP_DATA_DIR`.
- `arp -a` parser dependente de locale (B-16): impl por SO + regex testada.
- Endpoints duplicados / órfãos da v1.0 removidos (B-04).
- Histórico do device agora é persistido e consumido (B-12).

### Security
- Tokens (USCall, webhook) cifrados em repouso com Fernet derivado de
  `APP_SECRET_KEY` via HKDF.
- Mascaramento de tokens na UI (`••••••••`) e no JSON da API
  (apenas `"set"`/`null`).
- Rate-limit em login (5 falhas em 10min → bloqueio).
- Headers de segurança: `X-Content-Type-Options`, `Referrer-Policy`,
  `X-Frame-Options`, `Cache-Control: no-store` em rotas autenticadas.
- Cookies `HttpOnly`, `SameSite=Lax`, `Secure` (toggle por ambiente).
- Validação de IP por regex antes de qualquer subprocess.
- Updater: verificação SHA256 obrigatória + path-traversal guard no tar.

### Breaking
- Banco completamente novo. Não há upgrade direto v1.0 → v2.0.
- Endpoints renomeados:
  - `/api/devices/force-monitor` continua, mas agora exige cookie de sessão.
  - `/api/webhooks/test/{type}` exige cookie + CSRF token.
  - `/api/history/{name}` foi substituído por `/api/devices/{id}/history`.
- Token USCall em texto plano em `data/config.json` da v1.0 deve ser
  reinserido após migração (será rotacionado para criptografia em repouso).
