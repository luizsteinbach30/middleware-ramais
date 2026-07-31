# ADR-0003 — Múltiplos servidores USCall por instalação

Data: 2026-07-31 · Status: aceito · Release: v2.7.0

## Contexto

Clientes têm ambientes com **mais de um PBX USCall** (matriz + filiais). Até a
v2.6.0 a instalação suportava um único host (`uscall_host`/`uscall_token` em
`app_config`), então metade dos ramais ficava invisível para o middleware.

Premissa validada com o cliente: **os números de ramal NÃO se repetem entre os
servidores** de uma mesma instalação.

## Decisões

### 1. Tabela `uscall_servers` (não JSON em KV)

Servidores viram linhas de uma tabela própria (migration 0007): `nome`,
`host`, `token` (ciphertext `SecretBox`, mesma cifra da `app_config`),
`verify_ssl`, `enabled`. Motivos: token cifrado por linha, FK a partir de
`devices`, CRUD limpo na API sem parsing de JSON.

A migration converte a config KV legada no servidor **"Principal"** copiando o
ciphertext **verbatim** — a mesma `APP_SECRET_KEY` decripta, nenhum re-encrypt.
O KV legado permanece no banco (rollback barato) mas não é mais lido/escrito.

### 2. Merge por união simples

Como ramais são únicos globalmente, a coleta faz `fetch_extensions` de todos os
servidores habilitados **em paralelo** e concatena os payloads. Ramal duplicado
entre servidores (violação da premissa) gera `collect_duplicate_ramal` warning
e **o primeiro servidor vence** — determinístico pela ordem de cadastro (id).
`Device.name` continua único global; `devices.uscall_server_id` (FK SET NULL)
marca a origem da coleta.

### 3. Semântica de falha parcial

Um servidor fora do ar **não derruba a coleta**: vira
`collect_server_failed` warning e os demais seguem. Snapshot/webhook saem com a
união dos que responderam (coleta parcial é segura: o upsert só toca ramais
presentes no payload). **Todos** fora → sem snapshot e sem webhook, como a
falha total de hoje.

### 4. Webhook: campo aditivo, contrato preservado

O formato dos payloads `extensions` e `devices` continua o **array flat** que o
receptor aceita. Cada item ganha a chave **aditiva** `"uscall_server"` (nome do
servidor de origem; `null` em devices sem origem conhecida). Receptores que
ignoram chaves extras não são afetados.

### 5. Verificação de registro SIP consulta todos os servidores

`verify_registration_batch/one` consultam **todos** os servidores habilitados
em paralelo por tentativa e mesclam (união). Com ramais únicos, é equivalente a
rotear "pro servidor certo" sem o plumbing de propagar `uscall_server_id` pelo
apply — 1 request extra por servidor por tentativa, com N pequeno. Falha de um
servidor vira warning e a tentativa segue com os demais.

### 6. Dashboard

Permanece agregado. A visão por servidor fica no log da coleta
(`collect_ok` com contagem por servidor) e no teste de conexão por servidor na
tela `/config`. Breakdown visual é evolução futura se houver demanda.

## Consequências

- Instalações single-server seguem funcionando sem nenhuma ação (migration
  cria o "Principal" automaticamente).
- Remover um servidor **não** apaga os devices coletados dele — só desvincula
  a origem (`SET NULL`).
- Se a premissa "ramais únicos" cair no futuro, o caminho é chave composta
  `(uscall_server_id, name)` em `devices` + desambiguação no webhook — fora de
  escopo enquanto o cliente garantir faixas distintas.

## Relacionados

- ADR-0002 (extension configurator)
- `docs/WEBHOOK_ARQUITETURA.md` (contrato do receptor)
