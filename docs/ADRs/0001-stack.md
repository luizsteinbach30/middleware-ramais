# ADR-0001: Stack Python/FastAPI multiplataforma com SQLite e auto-update por tags

## Contexto

A v1.0 era uma prova de conceito Python/FastAPI persistindo em arquivos JSON,
sem auth, com scheduler duplicado e código órfão. Precisamos transformar em
produto distribuível para servidores de cliente, com suporte Windows + Linux,
auto-update e segurança aceitável para produção.

## Decisão

- **Linguagem/framework**: Python 3.11+ + FastAPI (continuidade com a v1.0,
  permite reusar mental model do time, async maduro).
- **Persistência**: SQLite em modo WAL via SQLAlchemy 2.0 + Alembic. Banco
  embutido elimina dependência de infra externa nos servidores cliente.
- **Suporte de SO**: Windows e Linux com coletores de rede (ping/arp)
  abstraídos por interface, implementação concreta selecionada em runtime.
- **Auto-update**: pull periódico de tags estáveis no GitHub Releases, com
  verificação SHA256 obrigatória, layout `app/<versão>/` + symlink/junction
  `current`, rollback automático em falha de healthcheck pós-restart.
- **Auth**: login local com bcrypt, sessões em DB, cookie HttpOnly+SameSite,
  CSRF token nas mutações.
- **Scheduler**: APScheduler `AsyncIOScheduler` único; nada de threads
  paralelas concorrendo.
- **Servidor**: 1 worker Uvicorn (estado consistente em memória para o
  scheduler).

## Consequências

- O sistema fica auto-contido e instalável com 1 comando.
- Não escalamos horizontalmente em um único host (1 worker), mas isso é
  aceitável: cada servidor cliente tem 1 instância e coleta a sua própria
  tenancy do USCall.
- Migrations Alembic precisam ser sempre reversíveis para rollback funcionar.
- Tokens em repouso ficam cifrados com chave derivada de `APP_SECRET_KEY` —
  perder a chave equivale a perder os tokens, requer plano de cofre.

## Alternativas consideradas

- **PostgreSQL externo**: descartado — infra extra sem benefício neste
  cenário.
- **Push via webhook do GitHub para o cliente**: descartado — exige IP/porta
  acessível do GitHub, geralmente inviável em LAN do cliente.
- **Sempre `main` como fonte do update**: descartado — sem janela de
  validação por canal.
- **Múltiplos workers Uvicorn**: descartado para esta versão pelo custo de
  coordenação do scheduler.
