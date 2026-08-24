# ADR-0006 — Backup e restauração

Data: 2026-08-24 · Status: aceito · Release: v2.9.0 (em desenvolvimento)

## Contexto

Até a v2.8.1 não havia como tirar a configuração do sistema de dentro do banco.
O único export existente era por **ambiente**, um arquivo de cada vez
(`.mwrenv`, PR de 2026-06), e nada mais saía: retenções, webhooks, servidores
USCall, brokers MQTT, usuários e o cadastro de devices só existiam no
`app.db`. A pasta `backups/` era criada no boot desde a v2.0 e nunca recebia um
arquivo — o "backup" na prática era copiar o `.db` à mão, com o serviço no ar.

O pedido do dono (2026-08-24) tem as duas metades: *"exportar e importar os
ambientes em outro sistema"* e *"colocar essa rotina de gerar um backup"*.

## Decisões

### 1. Dois artefatos, porque são dois problemas

| | Pacote portável (`.mwrbak`) | Snapshot (`.db.gz`) |
|---|---|---|
| Responde | "levar a configuração para outra instalação" | "recuperar esta instalação" |
| Conteúdo | configuração, sem histórico | o banco inteiro |
| Segredos | recifrados com a passphrase do arquivo | como estão, cifra local |
| Aplicação | imediata, por seção, em transação | troca do banco no boot |

Um só artefato não serve: o snapshot não é portável (a cifra dos segredos é
local, e ele traz junto ledger e histórico de outra operação), e o pacote não
recupera dado nenhum.

### 2. Os segredos viajam em claro **dentro** do envelope cifrado

`SecretBox` deriva a chave do `APP_SECRET_KEY` da máquina. Exportar o
ciphertext seria exportar bytes que o destino nunca conseguiria ler. Então o
pacote decifra na origem e recifra no destino, e a proteção do arquivo passa a
ser a passphrase (`core.export_crypto`: PBKDF2-SHA256 200 mil iterações +
Fernet — a mesma cifra do export por ambiente, já em produção).

Consequência aceita: quem tem o arquivo e a passphrase tem o token do USCall, a
senha do broker e a senha SIP de cada ramal. Por isso a API **recusa exportar
sem passphrase** (não existe "exportar sem cifrar"), e o endpoint exige admin.

A passphrase do backup automático fica no `app_config` cifrada pela `SecretBox`
local e é a única chave que **não** entra no pacote: exportá-la entregaria,
junto com o backup, a chave dos próprios backups.

### 3. Restaurar o banco não troca o arquivo a quente

Substituir `app.db` com o processo escrevendo nele é corrupção quase garantida
no Windows (arquivo aberto) e é errado em qualquer plataforma: jobs e o coletor
MQTT continuariam com o banco antigo em memória e o WAL apontaria para o
arquivo trocado.

A restauração então **agenda**: o arquivo é validado, descomprimido para
`db/restore.pending.db`, e `core.db.init_engine` faz a troca no próximo boot,
antes da primeira conexão. É o único instante em que ninguém tem o arquivo
aberto. O hook só roda quando a URL vem das settings — com URL explícita
(testes, alembic apontado a outro banco) não se mexe em nada.

O custo é pedir um reinício ao operador. Aceito: o alternativo seria um modo de
manutenção que derruba a API, para no fim das contas também interromper o
serviço.

### 4. Validar antes de aceitar, e recusar backup do futuro

Antes de virar pendência, o arquivo passa por: assinatura `SQLite format 3`,
`PRAGMA integrity_check`, presença das tabelas obrigatórias (`app_config`,
`users`, `devices`, `alembic_version`) e **revisão Alembic conhecida por esta
versão do código**. Revisão desconhecida = backup gerado por versão mais nova;
restaurá-lo traria um schema que o código instalado não sabe ler. Isso é erro,
não aviso — a saída é atualizar o middleware primeiro.

### 5. O banco substituído não é apagado

A troca move o banco atual para `backups/pre-restore-<data>.db`. Ele conta no
teto de espaço, mas a poda por quantidade nunca o remove: é a única forma de
desfazer uma restauração feita por engano.

### 6. Poda por quantidade **e** por espaço, preservando o último

O banco com ledger cresce rápido; sete cópias de um banco grande enchem o disco
sem ninguém perceber. A poda respeita `keep` (padrão 7) e `max_mb` (padrão
2048), aplicando o corte que vier primeiro — mas **nunca apaga o backup mais
recente**, mesmo que sozinho ele estoure o teto. Ficar sem cópia nenhuma por
causa de um limite de espaço seria o pior resultado possível.

### 7. Import nunca apaga conta de acesso

Em `replace`, ambientes, servidores USCall e brokers são substituídos. Usuários
e devices, não: os que faltam são criados e (em `replace`) os existentes têm
senha e perfil atualizados. Uma restauração que remove o login de quem está
operando deixa a instalação inacessível — e devices a coleta recria sozinha.

## Consequências

- Migração entre máquinas vira procedimento de tela, documentado no RUNBOOK §10.
- `update_broker` ganhou `clean_session`/`client_id` (não têm campo na tela) —
  sem eles a instalação restaurada assinaria o broker como cliente novo e
  perderia o backlog da sessão durável.
- Cada `.mwrbak` é um arquivo com credenciais reais circulando fora do sistema.
  A tela diz isso em texto; a alternativa (exportar mascarado) tornaria o
  arquivo inútil para provisionar telefone no destino.
