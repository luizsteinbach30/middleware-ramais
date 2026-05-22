# Documento de Requisitos — Middleware USCall Monitor

**Versão:** 2.0 (proposta de refatoração)
**Data:** 2026-05-09
**Status:** rascunho para aprovação
**Autor:** time de engenharia

---

## 1. Sumário executivo

O Middleware USCall Monitor é uma aplicação instalada em servidores de clientes que:

1. Coleta o status de ramais SIP da API do **USCall** (`/api/extenstatus`).
2. Monitora a disponibilidade de rede dos dispositivos (ping/arp).
3. Persiste histórico local e envia eventos para sistemas externos via **webhook**.
4. Expõe uma **UI web** (Tailwind + Jinja2) para visualização e configuração.
5. Atualiza-se automaticamente a partir de **releases (tags)** publicados no GitHub.

A versão 1.0 atual funciona como prova de conceito (arquivos JSON, sem auth, scheduler duplicado, código órfão). A versão 2.0 reescreve a fundação para ser **multiplataforma, escalável, segura e auto-atualizável**, mantendo o domínio (ramais, coletas, webhooks).

---

## 2. Stakeholders e personas

| Persona | Necessidade |
|---|---|
| **Administrador local** | Configurar host/token USCall, definir webhooks, ver alertas, forçar coletas. |
| **Operador NOC** | Visualizar status agregado, identificar ramal offline rapidamente. |
| **Sistema externo (Base44 etc.)** | Receber eventos via webhook autenticado, com payload padronizado. |
| **Equipe de produto (vendor)** | Publicar releases no GitHub e ter confiança que clientes vão consumir a atualização sem ação manual. |

---

## 3. Decisões arquiteturais (ADRs resumidos)

| # | Decisão | Justificativa |
|---|---|---|
| ADR-01 | **SO alvo: Windows + Linux** | Base de clientes mista. Coletores de rede abstraídos por interface, com implementação por SO. |
| ADR-02 | **Persistência: SQLite (WAL mode)** | Banco embutido, atômico, suporta concorrência leitura/escrita, queries SQL para histórico, backup = copiar arquivo. Substitui todos os JSON de `data/`. |
| ADR-03 | **Auto-update: pull periódico de tags estáveis** | Watcher local consulta a API do GitHub e instala apenas releases assinados (canal `stable` ou `beta`). |
| ADR-04 | **Autenticação: login local com usuário/senha** | Hash com bcrypt, sessão por cookie HttpOnly+SameSite, primeira senha gerada na instalação. Tokens sensíveis (USCall, webhooks) mascarados na UI. |
| ADR-05 | **Scheduler único: APScheduler** | Remove o `threading.Thread` paralelo. Um único agendador controla todos os jobs. |
| ADR-06 | **Servidor: Uvicorn 1 worker** | Jobs precisam de estado em memória consistente; múltiplos workers exigiriam coordenação extra. Concorrência interna via async + threadpool. |
| ADR-07 | **Configuração: pydantic-settings** | `.env` para infra (porta, paths, DB), tabela `app_config` para configuração de domínio editável pela UI. |

---

## 4. Stack tecnológica

### 4.1 Backend
| Camada | Tecnologia | Versão alvo |
|---|---|---|
| Linguagem | Python | 3.11+ |
| Framework web | FastAPI | ≥ 0.110 |
| ASGI server | Uvicorn | ≥ 0.27 |
| ORM | SQLAlchemy | 2.0 (estilo `Mapped`) |
| Migrations | Alembic | ≥ 1.13 |
| DB | SQLite | ≥ 3.40 (WAL) |
| Settings | pydantic-settings | ≥ 2.x |
| Scheduler | APScheduler | 3.x |
| Logs | structlog | ≥ 24.x (JSON em prod) |
| HTTP client | httpx | ≥ 0.27 |
| Templates | Jinja2 | ≥ 3.x |
| Auth | passlib[bcrypt] + itsdangerous | atual |
| Testes | pytest, pytest-asyncio, httpx test client | atual |
| Lint/Format | ruff, mypy | atual |

### 4.2 Frontend
- Tailwind via CDN (curto prazo) → bundle local (médio prazo, sem dependência de internet pública).
- Chart.js via CDN inicialmente; idem.
- HTML server-rendered + ilhas de JS vanilla (sem framework SPA).
- Font Awesome 6.

### 4.3 Empacotamento e deploy
| Item | Windows | Linux |
|---|---|---|
| Serviço | NSSM (recomendado) ou pywin32 service | systemd unit |
| Auto-start | service `auto` | `systemctl enable` |
| Logs SO | EventLog opcional | journald |
| Atualizador | task agendada PowerShell + serviço watcher | systemd timer + serviço watcher |
| Diretório base | `C:\ProgramData\MiddlewareMonitor\` | `/var/lib/middleware-monitor/` |
| Binário/código | `C:\Program Files\MiddlewareMonitor\app\<versão>\` | `/opt/middleware-monitor/app/<versão>/` |

### 4.4 CI/CD (GitHub)
- Branches: `main` (estável), `develop` (integração), `feature/*`, `hotfix/*`.
- Tags: `vMAJOR.MINOR.PATCH` (semver).
- GitHub Actions: lint → testes → build → publica `Release` com `app-vX.Y.Z.tar.gz` + `SHA256SUMS` + `SHA256SUMS.sig` (assinatura GPG opcional).
- Canais: `stable` (releases sem flag de pré-release), `beta` (`prerelease=true`).

---

## 5. Arquitetura proposta

### 5.1 Visão de pacotes

```
src/middleware_monitor/
├── __init__.py
├── __main__.py                 # entry: `python -m middleware_monitor`
├── version.py                  # __version__ = "2.0.0"
├── app.py                      # FastAPI factory + lifespan
├── settings.py                 # pydantic-settings (.env)
├── core/
│   ├── db.py                   # engine + sessionmaker + get_session()
│   ├── models.py               # tabelas SQLAlchemy
│   ├── migrations/             # Alembic
│   ├── security.py             # hash de senha, sessão assinada, deps de auth
│   ├── logging.py              # structlog config
│   └── scheduler.py            # APScheduler único
├── domain/
│   ├── auth/         (services + schemas)
│   ├── config/       (CRUD de app_config)
│   ├── devices/
│   ├── collections/
│   └── webhooks/
├── integrations/
│   ├── uscall_client.py
│   ├── network/
│   │   ├── base.py             # Protocol PingProbe / ArpProbe
│   │   ├── windows.py
│   │   └── linux.py
│   └── webhook_sender.py
├── jobs/
│   ├── collect_extensions.py
│   ├── monitor_devices.py
│   └── retention.py            # poda webhook_events, system_logs, device_pings
├── api/                        # JSON
│   ├── deps.py
│   ├── auth.py
│   ├── dashboard.py
│   ├── devices.py
│   ├── collections.py
│   ├── webhooks.py
│   ├── config.py
│   ├── logs.py
│   └── system.py               # /healthz, /readyz, /version
├── web/                        # HTML
│   ├── pages.py
│   ├── templates/
│   └── static/
└── updater/
    ├── client.py               # consulta GitHub Releases
    ├── installer.py            # download + verify + extract + migrate + restart
    └── service.py              # job APScheduler
tests/
  unit/  integration/  e2e/
docs/
  REQUISITOS.md  TELAS.md  ARQUITETURA.md  INSTALACAO.md  RUNBOOK.md
scripts/
  install_windows.ps1  install_linux.sh  bootstrap_admin.py
packaging/
  windows/  linux/
.github/workflows/
  ci.yml  release.yml
```

### 5.2 Fluxos principais

**Coleta de ramais (job `collect_extensions`):**
1. Lê `uscall_host` / `uscall_token` de `app_config`.
2. Chama `GET https://{host}/api/extenstatus?token=...&tipo=all` via `httpx` com `verify=True` por padrão (toggle `uscall_verify_ssl` em config).
3. Persiste o snapshot em `collections` (blob JSON + hash + timestamp).
4. Faz upsert em `devices` (atualiza IP/status lógico/last_seen).
5. Dispara webhook `extensions` se habilitado.
6. Atualiza `system_logs` e métrica `last_collection_at`.

**Monitor de rede (job `monitor_devices`):**
1. Carrega devices ativos (`logical_status='available'`).
2. Para cada IP roda `PingProbe.ping(ip)` (impl por SO, timeout configurável, paralelismo limitado por semáforo).
3. Atualiza `devices.network_status`, `latency`, `last_ping`.
4. Insere ponto em `device_pings` (com retenção configurável em dias).
5. Dispara webhook `devices` com snapshot consolidado.

**Atualização automática (job `updater`):**
1. A cada `update_check_interval_minutes`:
2. Consulta `GET https://api.github.com/repos/<owner>/<repo>/releases?per_page=10`.
3. Filtra pelo canal (`stable`/`beta`) e versão > `__version__`.
4. Baixa `app-vX.Y.Z.tar.gz` + `SHA256SUMS`.
5. Verifica SHA256 (e GPG se habilitado).
6. Extrai em `app/<nova-versão>/`.
7. Roda `alembic upgrade head`.
8. Atualiza symlink/junction `current` → `app/<nova-versão>/`.
9. Solicita ao supervisor (NSSM/systemd) reinício do serviço.
10. Em falha: marca update como falho em `update_history`, mantém versão anterior, gera webhook `system` opcional.

### 5.3 Modelo de dados (SQLite)

```
users
  id PK
  username UNIQUE
  password_hash
  role            -- admin | operator (futuro)
  created_at
  last_login_at

app_config
  key PK
  value           -- JSON serializado
  is_secret       -- mascarar na UI/API
  updated_at
  updated_by FK users.id

devices
  id PK
  name UNIQUE     -- nº do ramal
  ip
  mac
  model           -- detectado por fingerprint (opcional)
  logical_status  -- available | unavailable | unknown
  network_status  -- online | offline | unknown
  latency_ms
  last_seen_at
  last_ping_at
  notes
  created_at
  updated_at

device_pings                -- histórico para gráficos
  id PK
  device_id FK CASCADE
  timestamp
  online BOOL
  latency_ms
  INDEX (device_id, timestamp DESC)

collections                 -- snapshots USCall
  id PK
  type            -- 'extensions' | 'results' | ...
  collected_at
  payload JSON
  payload_hash
  INDEX (type, collected_at DESC)

webhook_events
  id PK
  timestamp
  event_type      -- extensions | devices | results | system | test
  url
  http_status
  duration_ms
  success BOOL
  error
  payload JSON
  response_body
  INDEX (event_type, timestamp DESC)

system_logs
  id PK
  timestamp
  level           -- DEBUG | INFO | WARN | ERROR
  module
  message
  context JSON
  INDEX (timestamp DESC)

update_history
  id PK
  timestamp
  from_version
  to_version
  channel
  status          -- success | failed | rolled_back
  error
  duration_ms

sessions  (server-side, opcional)
  token PK        -- random 32B
  user_id FK
  created_at
  expires_at
  user_agent
  ip
```

### 5.4 Configuração

**`.env` (infra, lido por pydantic-settings):**
```
APP_HOST=0.0.0.0
APP_PORT=8080
APP_DATA_DIR=/var/lib/middleware-monitor          # ou C:\ProgramData\MiddlewareMonitor
APP_DB_URL=sqlite:///${APP_DATA_DIR}/db/app.db
APP_SECRET_KEY=<gerado na instalação>
APP_LOG_LEVEL=INFO
APP_UPDATE_REPO=org/middleware-monitor
APP_UPDATE_CHANNEL=stable
APP_UPDATE_CHECK_MINUTES=60
APP_UPDATE_PUBLIC_KEY_PATH=/etc/middleware-monitor/release.pub  # opcional
```

**Tabela `app_config` (domínio, editável pela UI):**
```
client_code
uscall_host
uscall_token                (secret)
uscall_verify_ssl           (bool)
extensions_interval_seconds
devices_interval_seconds
results_interval_seconds
ping_timeout_ms
ping_concurrency
device_ping_retention_days
webhook_log_retention_days
webhooks.extensions.url
webhooks.extensions.token   (secret)
webhooks.extensions.enabled
webhooks.devices.url
webhooks.devices.token      (secret)
webhooks.devices.enabled
webhooks.results.url
webhooks.results.token      (secret)
webhooks.results.enabled
```

---

## 6. Requisitos funcionais (RF)

### 6.1 Autenticação e usuários
- **RF-01** Na primeira instalação o sistema cria usuário `admin` e gera senha temporária; a UI obriga troca no primeiro login.
- **RF-02** Tela de login `/login` com usuário/senha. Bloqueio após 5 tentativas falhas em 10 min.
- **RF-03** Sessão persistida em cookie HttpOnly+SameSite=Lax, expiração de 12h e renovação por atividade.
- **RF-04** Endpoint `POST /logout` invalida a sessão.
- **RF-05** Página `/account` permite trocar a própria senha.

### 6.2 Configuração (telas e API)
- **RF-06** UI `/config` exibe e edita `client_code`, integração USCall, intervalos e webhooks.
- **RF-07** Tokens sensíveis nunca retornam em texto plano após salvos: a UI mostra `••••••••` e o usuário precisa marcar "alterar" para sobrescrever.
- **RF-08** Validação: intervalos mínimos 10s; `uscall_host` precisa ser host válido sem `https://`.
- **RF-09** Botão "Testar conexão USCall" usa `POST /api/uscall/test` retornando `{success, http_status, latency_ms, error?}`.
- **RF-10** Botão "Testar webhook" para cada tipo posta um payload `test=true`.

### 6.3 Coleta e dispositivos
- **RF-11** Job `collect_extensions` roda no intervalo configurado e cria registro em `collections`.
- **RF-12** Atualiza tabela `devices` por `name` (upsert): cria novo se `status='disponivel'` e tem IP; atualiza IP/status lógico se já existe.
- **RF-13** Job `monitor_devices` faz ping em todos os devices com IP, em paralelo limitado, escreve `device_pings` e atualiza `devices`.
- **RF-14** Endpoint `GET /api/devices` retorna lista filtrável por `network_status`, `logical_status`, `name`.
- **RF-15** Endpoint `POST /api/devices/{id}/refresh` força ping isolado.
- **RF-16** Endpoint `GET /api/devices/{id}/history?from=...&to=...&granularity=...` retorna agregação para o gráfico.
- **RF-17** Endpoint `POST /api/devices/force-monitor` força um ciclo completo (admin only, rate-limit 1/min).

### 6.4 Coletas / histórico
- **RF-18** UI `/collections` lista snapshots paginados (50 por página) com filtro por intervalo de data e tipo.
- **RF-19** Endpoint `GET /api/collections?type=&from=&to=&page=&size=` paginado.
- **RF-20** Endpoint `GET /api/collections/{id}` retorna o payload completo.
- **RF-21** Job de retenção remove `collections` mais antigas que `collection_retention_days`.

### 6.5 Webhooks
- **RF-22** `webhook_sender` faz POST com header `Authorization: Bearer <token>` quando token presente, `Content-Type: application/json`, timeout configurável.
- **RF-23** Em falha de rede, faz **retry com backoff exponencial** (3 tentativas: 0s, 5s, 30s). Cada tentativa registra um evento.
- **RF-24** UI `/webhook-logs` lista os envios paginados, com filtro por tipo, status, intervalo. Permite ver payload e response.
- **RF-25** Job de retenção remove `webhook_events` mais antigos que `webhook_log_retention_days`.

### 6.6 Logs e dashboard
- **RF-26** UI `/` (Dashboard) exibe cards: total devices, network online/offline, lógico avail/unavail, latência média/máxima, última coleta, contagem de webhooks por status, versão atual, status do updater.
- **RF-27** UI `/logs` mostra `system_logs` paginados, filtráveis por nível e módulo.
- **RF-28** Endpoint `GET /api/system/healthz` retorna `200 {"status":"ok"}` sem auth (liveness).
- **RF-29** Endpoint `GET /api/system/readyz` retorna 200 quando DB e scheduler estão ativos.
- **RF-30** Endpoint `GET /api/system/version` retorna versão atual, canal, próxima versão disponível e horário do último check.

### 6.7 Auto-update
- **RF-31** Job `updater` consulta GitHub a cada `update_check_interval_minutes` e registra em `update_history`.
- **RF-32** Quando há nova versão no canal, baixa, valida hash, extrai em diretório versionado, roda `alembic upgrade head`, troca symlink `current` e solicita restart do serviço.
- **RF-33** UI `/system/updates` permite ver histórico, mudar canal (`stable`/`beta`), pausar updates automáticos, e disparar "Verificar agora" (admin only).
- **RF-34** Em falha de migração ou inicialização da nova versão, o supervisor faz **rollback automático** para a versão anterior e marca `update_history.status='rolled_back'`.
- **RF-35** Releases publicados pela equipe contêm sempre `SHA256SUMS`. A verificação de assinatura GPG é opcional (configurável).

---

## 7. Requisitos não-funcionais (RNF)

### 7.1 Desempenho
- **RNF-01** UI deve carregar em <500ms para até 1000 devices em rede local.
- **RNF-02** `monitor_devices` deve completar em <60s para 200 devices (paralelismo 20).
- **RNF-03** `collect_extensions` deve tolerar resposta USCall >5MB sem travar (streaming).

### 7.2 Confiabilidade
- **RNF-04** Apenas um worker do scheduler (`AsyncIOScheduler`) ativo. Locks por job (`max_instances=1`).
- **RNF-05** Escritas no banco transacionais; falha não corrompe estado.
- **RNF-06** Reinicialização do serviço não perde mais que o ciclo em andamento.

### 7.3 Segurança
- **RNF-07** Hash de senha via `bcrypt` (cost ≥ 12) ou `argon2id`.
- **RNF-08** Cookies de sessão `Secure` em produção (configurável), `HttpOnly`, `SameSite=Lax`.
- **RNF-09** Headers de segurança: `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options=DENY`.
- **RNF-10** CSRF protection nos endpoints de mutação chamados pela UI (token em formulário ou header).
- **RNF-11** Tokens secretos em `app_config` armazenados criptografados em repouso (chave derivada de `APP_SECRET_KEY`).
- **RNF-12** `verify=True` em todas as chamadas HTTPS por padrão; toggle explícito em config.
- **RNF-13** Logs nunca contêm o valor de `uscall_token` ou tokens de webhook.
- **RNF-14** Rate limit em `/api/auth/login` (10/min/IP), `/api/uscall/test` (1/min/IP), `/api/devices/force-monitor` (1/min/usuário).
- **RNF-15** Toda chamada de subprocess usa lista de argumentos (sem `shell=True`); inputs IP validados por regex.

### 7.4 Observabilidade
- **RNF-16** Logs estruturados JSON em produção: `timestamp, level, module, event, context`.
- **RNF-17** Endpoint `/metrics` Prometheus opcional (último ciclo, pings ok/falha, webhook ok/falha, versão).
- **RNF-18** Toda exceção não tratada gera `system_logs.level=ERROR` com stack trace truncado (sem dados sensíveis).

### 7.5 Compatibilidade
- **RNF-19** Suportar Windows 10+/Server 2019+ e Linux glibc≥2.31 (Debian 11+, Ubuntu 20.04+).
- **RNF-20** Funcionar em redes IPv4; IPv6 aceito mas opcional.
- **RNF-21** Browsers suportados: Chrome/Edge últimas 2 versões, Firefox últimas 2.

### 7.6 Operacional
- **RNF-22** Backup do estado = copiar `${APP_DATA_DIR}/db/app.db` (com `VACUUM INTO`).
- **RNF-23** Desinstalador remove serviço e pode preservar `data/` opcionalmente.
- **RNF-24** Logs roteados para journald (Linux) e EventLog (Windows) com tag única `MiddlewareMonitor`.

---

## 8. Bugs e dívidas detectados na v1.0 (e como resolver na v2.0)

| # | Problema atual | Onde | Tratamento na v2.0 |
|---|---|---|---|
| B-01 | `core/scheduler.py` (APScheduler) não é usado; o que roda é um `threading.Thread` em `monitor_service.py:270`. | duplicidade | Único scheduler em `core/scheduler.py` baseado em APScheduler. Thread removida. |
| B-02 | `services/collector_service.py` (versão async) duplica `monitor_service.collect_extensions` e nunca é chamado. | morto | Substituir pela versão refatorada em `jobs/collect_extensions.py`; remover. |
| B-03 | `services/ping_service.py`, `arp_service.py`, `fingerprint_service.py` não são usados. | morto | Mover lógica para `integrations/network/` com testes. |
| B-04 | `api/dashboard.py`, `api/devices_page.py`, `api/collections_page.py`, `api/collections_routes.py`, `api/logs_routes.py`, `api/runtime_routes.py`, `api/uscall_test.py` não são incluídos em `main.py`. | morto/parcial | Remover duplicatas; manter um único router por recurso. |
| B-05 | Dois `load_config` (em `core/config.py` e `services/monitor_service.py:18`) com defaults diferentes. | divergência | Único módulo de settings/config; demais usam o repositório de `app_config`. |
| B-06 | `core/logger.py` faz read+write não atômico em `data/logs.json` — corre risco de corrupção sob concorrência. | race | Logs em tabela `system_logs` (transação SQLite). |
| B-07 | `webhook_logs.py` aplica retenção a cada escrita reescrevendo o arquivo inteiro — O(n) por evento. | perf/IO | Tabela `webhook_events` + job `retention` periódico (DELETE). |
| B-08 | `templates/devices.html` chama `/api/devices/` mas o router é `/api/devices` (sem `/`). FastAPI faz 307; POST com redirect pode perder body. | sutil | Definir prefixos consistentes e front sempre sem barra final. |
| B-09 | `services/monitor_service.py` chama `requests.get(..., verify=False)` para USCall. | segurança | `verify=True` por padrão; toggle explícito em config. |
| B-10 | Token USCall e webhook em `data/config.json` em texto plano e commitado. | segurança | Criptografia em repouso + `.gitignore` para `data/`. Rotacionar tokens existentes. |
| B-11 | `monitor_devices` faz pings sequenciais → ruim para >50 ramais. | perf | `asyncio.gather` com `Semaphore` ou pool de threads. |
| B-12 | `device_chart.html` consome `/api/history/{name}` que lê `data/history/{name}.json`, mas nada grava esse arquivo. | bug | Endpoint passa a ler `device_pings` no DB; histórico passa a existir de fato. |
| B-13 | `api/runtime_routes.py` lê `data/runtime_status.json` que nunca é escrito. | bug | Substituir por `/api/system/version` + status do scheduler. |
| B-14 | `webhook_logs.py:apply_retention` filtra com `datetime.now()` local, sem timezone. | sutil | Padronizar UTC em todo o sistema; UI converte para timezone do usuário. |
| B-15 | Sem CSRF em `/api/config/`. | segurança | Adicionar CSRF token nas mutações via UI. |
| B-16 | Subprocess `arp -a` parseia formato dependente do locale do SO. | regressão silenciosa | Implementação explícita por SO + testes com fixtures. |
| B-17 | `start_monitor` chama `print` em vez de logger. | ergonomia | Logger estruturado. |
| B-18 | Sem testes automatizados. | qualidade | Suite mínima `unit/` + `integration/` (DB temporária) + `e2e/` (TestClient). |
| B-19 | `logs.json` cresce até 500 entradas e nunca tem retenção temporal. | semântica | `system_logs` com retenção por dias. |
| B-20 | Caminhos relativos (`data/...`) dependem do CWD ao iniciar — quebra ao rodar como serviço. | bug | Resolver tudo a partir de `APP_DATA_DIR`. |

---

## 9. Auto-update — protocolo detalhado

### 9.1 Estrutura de release
Cada release no GitHub contém:
```
app-vX.Y.Z.tar.gz       # código + dependências (wheels) ou requirements.lock
SHA256SUMS              # hash do tar.gz
SHA256SUMS.asc          # assinatura GPG (opcional)
RELEASE_NOTES.md        # gerado a partir do CHANGELOG
```

### 9.2 Layout em disco do cliente

**Linux:**
```
/opt/middleware-monitor/
├── current  -> app/2.0.3/        (symlink)
├── app/
│   ├── 2.0.2/
│   ├── 2.0.3/
│   └── 2.0.4/
└── venv/                         # virtualenv compartilhado
/var/lib/middleware-monitor/
├── db/app.db
├── db/app.db-wal
├── backups/
└── tmp/
/etc/middleware-monitor/
├── env
└── release.pub                   # chave pública GPG (se assinatura ativa)
/etc/systemd/system/middleware-monitor.service
/etc/systemd/system/middleware-monitor-updater.service
```

**Windows:**
```
C:\Program Files\MiddlewareMonitor\
├── current\         (junction)
├── app\2.0.3\
├── app\2.0.4\
└── venv\
C:\ProgramData\MiddlewareMonitor\
├── db\app.db
├── backups\
└── tmp\
```

### 9.3 Sequência de update

```
1. Watcher chama GET https://api.github.com/repos/{repo}/releases (auth opcional via PAT em .env).
2. Filtra por canal:
     stable -> prerelease=false E draft=false
     beta   -> draft=false
3. Pega a maior versão semver > __version__.
4. Baixa app-vX.Y.Z.tar.gz e SHA256SUMS para tmp/.
5. Verifica:
     - sha256(arquivo) == valor em SHA256SUMS
     - (opcional) gpg --verify SHA256SUMS.asc SHA256SUMS
6. Extrai em app/<X.Y.Z>/.
7. Reusa venv compartilhado:
     pip install --no-deps -r app/<X.Y.Z>/requirements.lock
8. Roda alembic upgrade head usando o DB existente.
9. Faz backup atômico do DB (sqlite VACUUM INTO backups/<ts>-<from>->.<to>.db).
10. Atualiza symlink/junction current -> app/<X.Y.Z>.
11. Solicita restart do serviço:
     Linux:   systemctl restart middleware-monitor
     Windows: nssm restart MiddlewareMonitor (ou via SCM)
12. Health-check pós-restart com timeout 60s em GET /api/system/healthz.
13. Sucesso -> registra em update_history.
14. Falha   -> rollback (symlink volta) + restart + update_history.status='rolled_back'.
15. Retém últimas 3 versões em app/; demais são removidas.
```

### 9.4 Considerações de segurança
- O watcher roda como serviço dedicado com permissão de escrita só em `app/`, `tmp/`, `backups/`.
- Nunca executa código baixado antes de validar checksum.
- O usuário pode desativar updates automáticos (`auto_update_enabled=false`) e disparar manualmente.

---

## 10. Critérios de aceite globais

- [ ] Instalador Windows e Linux funcionam em VM limpa com 1 comando.
- [ ] Primeiro acesso obriga troca de senha do `admin`.
- [ ] Configuração antiga (`data/config.json` v1.0) é importada via comando `mm-migrate-from-json`.
- [ ] Job de monitoramento mantém 200 ramais com latência média <2ms em ambiente típico.
- [ ] Update do `v2.0.0 → v2.0.1` em ambiente de teste executa fim-a-fim sem intervenção, incluindo migration.
- [ ] Falha simulada na nova versão dispara rollback automático e o serviço volta a rodar.
- [ ] Suite de testes cobre ≥70% dos módulos `domain/`, `integrations/network/`, `updater/`.
- [ ] Sem segredos em texto plano em arquivos de config commitados.
- [ ] `/healthz` e `/readyz` respondem corretamente em <100ms.

---

## 11. Roadmap sugerido (fases)

| Fase | Escopo | Resultado |
|---|---|---|
| **F1 — Fundação** | Reorganizar pacote `src/`, settings, logging, DB+Alembic, models, scheduler único, testes mínimos. | App roda no novo layout com mesmo comportamento. |
| **F2 — Auth + UI revisada** | Login, sessão, troca de senha, mascarar tokens, CSRF, tela de logs no DB. | UI segura. |
| **F3 — Coletores e webhook** | Refatora coleta, monitor de rede assíncrono, webhook com retry, retenção. | Performance e resiliência. |
| **F4 — Auto-update** | Atualizador, layout em disco versionado, instalador Win/Linux, pipeline de release no GitHub. | Auto-update fim-a-fim. |
| **F5 — Hardening** | Métricas, headers de segurança, tarefas de retenção, documentação operacional, runbooks. | Pronto para escalar. |

---

## 12. Glossário

- **Ramal (extension):** ponto SIP gerenciado pelo USCall.
- **Coleta (collection):** snapshot de status de todos os ramais em um instante.
- **Device:** representação local do ramal/IP monitorado.
- **Webhook:** chamada HTTP POST que esta aplicação faz para um sistema externo.
- **Canal de update:** `stable` (releases publicados) ou `beta` (pré-releases).
- **APP_DATA_DIR:** diretório de dados persistentes (DB, backups, tmp).
- **Ambiente (Configurador):** agrupamento de ramais que compartilham um modelo
  de telefone e uma `config_padrao` (SIP server, credenciais, function keys etc).
- **Adapter (Configurador):** implementação por vendor (HTEK, Intelbras) que sabe
  reconhecer o aparelho, gerar o XML correto e fazer o upload pela web GUI.
- **Run / ExtensionApplyRun:** uma execução de aplicação em massa, registra
  `started_at`, `finished_at`, `total`, `ok`, `falha`, `operador`, `forcado`.

---

## 13. Módulo Configurador de Ramais (v2.2.0)

### Objetivo
Permitir provisionar telefones SIP em massa via web GUI dos próprios aparelhos,
agrupando-os em ambientes com configuração padrão compartilhada.

### Requisitos funcionais (RF-EC)

- **RF-EC-01** Cadastro de ambientes (CRUD) com nome único (slug) e modelo de
  telefone (lista `PHONE_MODELS`).
- **RF-EC-02** Para cada ambiente: planilha editável de linhas (ip, ramal, user
  auth, senha SIP, servidor SIP, número abreviado, nome visível).
- **RF-EC-03** `config_padrao` por ambiente armazenada como JSON em
  `extension_environments.config_padrao` e mesclada com defaults na leitura.
- **RF-EC-04** Pipeline minimalista de aplicação: ICMP ping opcional → send.
  Sem fingerprint/discover automático (modelo cadastrado é fonte da verdade).
- **RF-EC-05** Rolling delay (default 1s) entre disparos para evitar pico de
  rede; tracking ao vivo via polling do estado in-memory.
- **RF-EC-06** Por linha persiste `ultimo_status` (ok/erro), `ultimo_erro`,
  `ultimo_hash_aplicado`, `ultima_aplicacao`, `ultimo_modelo`, `ultimo_mac`.
- **RF-EC-07** Cada execução cria um `ExtensionApplyRun` com totais e operador.
- **RF-EC-08** Estados por linha: `pending` | `applied` | `outdated` | `error`,
  derivados do hash atual vs. último aplicado com OK.
- **RF-EC-09 (v2.2.1)** Seleção parcial: planilha tem coluna `✓` (checkbox).
  Endpoint `/apply` aceita `selected_ids` no body para reaplicar só linhas
  específicas (linhas selecionadas ignoram filtro de status). Atalhos:
  marcar todos, desmarcar, só erros/pendentes.
- **RF-EC-10 (v2.2.1)** Function Keys / DSS Keys editáveis na config padrão.
  Cada item: tecla (LineKey1..4), tipo (line / speed_dial / blf / disabled),
  label, account, valor (`linha` → coluna da planilha, `fixo` → string).
  HTEK força `account=0` (Account1) — campo escondido na UI.
- **RF-EC-11 (v2.2.1)** Detalhe de relatório: rota
  `/extension-configurator/runs/{id}` mostra cards (total/ok/falha/
  duração/operador) + snapshot atual das linhas (status, modelo, MAC,
  último erro). Endpoint `/api/.../runs/{id}/detail`.

### Requisitos não-funcionais (RNF-EC)

- **RNF-EC-01 — Whitelist anti-rede inviolável.** Adapters jamais emitem tags
  ou P-codes de rede (IP, máscara, gateway, DNS, DHCP, VLAN, VPN, 802.1X, QoS,
  Wi-Fi). Configs parciais preservam tudo que não é enviado. Validado por teste
  explícito em `tests/unit/extension_configurator/test_*.py`.
- **RNF-EC-02 — Auth obrigatória.** Endpoints GET exigem sessão; mutações
  exigem CSRF + `require_admin`.
- **RNF-EC-03 — Idempotência.** `compute_line_hash(env, line)` é determinístico;
  hash igual ao último aplicado com OK → linha não é reenviada (a menos que
  `force=True` ou seleção parcial pelo usuário).
- **RNF-EC-04 — Sessões DB curtas.** I/O com aparelho (segundos a dezenas)
  nunca segura transação aberta.
- **RNF-EC-05 — 1 worker Uvicorn.** Estado in-memory (`RunState`/`RowState`)
  é seguro porque não há concorrência entre workers.

### Adapters suportados

| Vendor | Modelos | Auth | Status |
|---|---|---|---|
| HTEK (HanLong) | UC902G, UC912 (v2.2.1), UC924 (v2.2.1) e família UC9xx | Basic/Digest auto | Validado em lab |
| Intelbras | V3001, V3101, V3501, V5501 | `md5(user:pwd:nonce)` + HTTP/1.0 | Validado em lab |

### Quirks de firmware tratados

- **HTEK URL-decode (v2.2.1)** — o firmware HanLong faz `urldecode(%XX)`
  no conteúdo de texto dos P-codes ao ler o XML. `vendors/htek.py` aplica
  `_htek_text()` (`urllib.parse.quote` + `xml_escape`) em **todos** os
  campos de texto (P3 DispalyName, P34 senha SIP, P35 SipUserId, P36
  AuthenticateID, P47 Sipserver, P30 NTP, P2 AdminPassword, P8681
  LogUser, softkey value/label). Sem isso, senhas com `%`, `&`, `<`, `>`
  viram lixo no aparelho.
- **HTEK softkey Account1 (v2.2.1)** — o firmware força `account=0`
  (Account1) nas softkeys; outros valores apontam para perfil
  inexistente e a tecla não disca. `_render_function_keys` ignora o
  valor da UI e grava `0`.
- **Intelbras escape de senha (v2.2.1)** — `_xml_escape_password()`
  escapa aspas (`"`→`&quot;`, `'`→`&apos;`) em `RegisterPswd` e em
  `web/account/Password` para evitar corrupção do valor armazenado.

### Tabelas novas

- `extension_environments(id PK slug, nome, modelo_telefone, config_padrao JSON, created_at, updated_at)`
- `extension_lines(id PK uuid, environment_id FK, ip, numero_ramal, user_auth, senha_sip, servidor_sip, numero_abreviado, nome_visivel, ultimo_*)`
- `extension_apply_runs(id PK, environment_id FK, started_at, finished_at, total, ok, falha, forcado, operador)`

Migration: `0002_extension_configurator` — reversível (up/down testados).

### ADR
Ver [ADR-0002](ADRs/0002-extension-configurator.md).
