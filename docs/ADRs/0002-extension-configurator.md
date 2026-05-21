# ADR-0002: Configurador de Ramais como módulo interno do middleware-monitor

Data: 2026-05-21
Status: Aceito (v2.2.0)

## Contexto

O projeto standalone `autocfg-ramais` (POC em Python/FastAPI + JSON files,
~2.300 LoC, 55 testes) entregou:

- Adapters HTEK UC902G (HanLong) e Intelbras V-series (V3001/V3101/V3501/V5501)
  validados em hardware lab com `send_config` ponta-a-ponta confirmado
  visualmente nos aparelhos.
- Pipeline de aplicação em massa com validação ICMP, rolling delay e tracking
  ao vivo (`RunState`/`RowState`).
- UI: planilha estilo Excel (Jspreadsheet CE) + telas Ambientes, Detail,
  Config, Relatórios.
- Engenharia reversa de protocolos legacy: auth `md5(user:pwd:nonce)` do
  Intelbras (descoberto no `login.html`), DSS Memory Key com sufixo `@N/f`
  embutido no Value (descoberto via diff de backup XML real).
- Defaults universais validados: `EnableKeyLock=2` + `KeyLockTimeout=30s`.

O middleware-monitor já provê toda a infra de produção que faltava: SQLite +
Alembic, auth bcrypt + cookie + CSRF, structlog, APScheduler, auto-update
via GitHub Releases, instalação multiplataforma, monitoramento de saúde.

## Decisão

Integrar o Configurador de Ramais como **módulo interno** do middleware-monitor,
não como serviço separado, preservando:

1. **Padrões arquiteturais existentes**: SQLAlchemy 2.0 com `Mapped[...]`,
   migrations Alembic, structlog, AsyncIOScheduler, single Uvicorn worker.
2. **Padrões de segurança**: auth obrigatória (cookie HttpOnly assinado),
   CSRF em mutações, `require_admin` para criar/aplicar.
3. **UI consistente**: server-rendered Jinja2 + Tailwind + ilhas de JS vanilla,
   sidebar com sub-bloco "Configurador de Ramais" sob border-top.
4. **Whitelist anti-rede inviolável** já validada no POC — codificada como
   conjunto fechado de campos permitidos por adapter, com teste explícito
   que falha se algum campo de rede vazar pra o XML enviado.

Estrutura interna:

```
src/middleware_monitor/
├── core/models.py                                       # +3 modelos ORM
├── core/migrations/versions/0002_extension_configurator.py
├── domain/extension_configurator/
│   ├── defaults.py        # config_padrao defaults + PHONE_MODELS
│   ├── schemas.py         # Pydantic v2
│   ├── repository.py      # CRUD sincrono
│   ├── service.py         # hash, statuses, picker
│   ├── apply.py           # pipeline async ping → send
│   └── run_state.py       # tracking in-memory
├── integrations/extension_configurator/
│   ├── __init__.py
│   └── vendors/
│       ├── base.py        # VendorAdapter (ABC)
│       ├── registry.py
│       ├── htek.py + htek_template.xml
│       └── intelbras.py + intelbras_template.xml
├── api/extension_configurator.py                        # 10 endpoints
└── web/
    ├── pages.py (rotas web)
    ├── static/js/pages/extension_configurator_*.js
    └── templates/extension_configurator/{list,detail,config,runs}.html
```

## Consequências

### Positivas
- Operadores ganham configurador no mesmo painel que já usam pra monitorar
  os ramais; mesmo login, mesmas permissões, mesmo deploy.
- Tracking de execuções persistido em `extension_apply_runs` — sobrevive a
  reinício do middleware (vs. JSON do POC que só tinha estado em memória).
- Auth + CSRF + admin role barram chamadas anônimas / CSRF cross-site.
- Release/update unificado: novos modelos de telefone entram via release
  do middleware (auto-update já existente).

### Negativas
- O middleware ganha responsabilidade extra. Bugs no Configurador podem,
  em teoria, afetar a estabilidade do scheduler/coleta. Mitigação: módulo
  está em `domain/extension_configurator/` totalmente isolado; pipeline
  de apply roda em `core.tasks.spawn` (background) e não bloqueia jobs.
- O modelo de DSS Speed Dial (`@N/f`) está validado só pra Intelbras V-series;
  BLF e outros subtipos ainda dependem de novos backups reais para descobrir
  os sufixos certos.
- Jspreadsheet CE entra via CDN nesta release — vendoring offline fica para
  release subsequente (importante para deploys air-gapped).

## Alternativas consideradas

1. **Manter `autocfg-ramais` como serviço separado** (rejeitada): duplicaria
   infraestrutura (auth, deploy, monitoring, update) e fragmentaria o painel
   do operador.
2. **Microservice independente comunicando via webhook** (rejeitada): overkill
   pra um módulo CRUD + aplicação síncrona; aumentaria a superfície de
   operações sem ganho proporcional.
3. **PR único grande** (rejeitada): risco alto de revisão difícil; optamos
   por 5 PRs incrementais (domain → vendors → services/API → UI → docs).

## Histórico
- 2026-05-19 a 2026-05-21: bootstrap, vendors, refinamentos no POC `autocfg-ramais`
- 2026-05-21: PRs 1-5 mergeados, release v2.2.0 publicada
