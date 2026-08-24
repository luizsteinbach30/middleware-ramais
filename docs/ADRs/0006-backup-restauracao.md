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

### 7. Importar compara antes de aplicar, e quem decide o empate é o operador

Pedido do dono em 2026-08-24, depois da primeira versão: sobrescrever calado é
inaceitável quando os dois lados são plausíveis. A importação passou a ter duas
etapas — `diff` e `apply`:

- **igual não vira escrita.** Se o item do arquivo bate campo a campo com o do
  banco, ele nem aparece na tela: não há decisão a tomar e gravar de novo só
  produziria ruído de auditoria (`updated_at` mexido sem nada ter mudado).
  Medido no banco real: 1929 dos 1930 devices caem nesse caso.
- **conflito vai para a tela** com os dois valores lado a lado e a escolha
  `atual` / `arquivo`, por item ou para o grupo inteiro.

Os dois lados da comparação saem da **mesma** função (`build`) que gera o
pacote: o que se compara é exatamente o que se aplica. Um segundo mapeamento só
para comparar divergiria do primeiro na primeira mudança de campo.

O padrão por grupo é `arquivo` — é o que "restaurar" quer dizer —, com uma
exceção: `users`. Ali o padrão é `atual`, inclusive no modo `replace`, porque um
padrão errado nesse grupo troca a senha de quem está operando e tranca a pessoa
para fora da instalação.

Valor de segredo não entra na comparação: token, senha de broker e hash de senha
aparecem como `••••` dos dois lados. A tela diz que difere, e isso basta para
decidir — mostrar o valor entregaria pela janela o que o envelope protege.

Identidade dos itens: `key` (config), `nome` (servidor/broker), `id` (ambiente),
`username`, `name` (device). É o que permite reconhecer o mesmo item numa
segunda importação — e é por isso que o ambiente é criado **com o id do
arquivo** em vez de ganhar um slug novo: sem id estável, todo import viraria uma
cópia e nunca haveria conflito para decidir.

### 8. Import nunca apaga conta de acesso nem device

`replace` apaga, nos grupos que aceitam (ambientes, servidores USCall, brokers
MQTT), o que existe no banco e não existe no arquivo. Usuários e devices ficam
fora dessa lista em qualquer modo: uma restauração que remove o login de quem
está operando deixa a instalação inacessível, e device a coleta REST recria
sozinha — apagar levaria junto histórico de ping e vínculo de linha.

Conta existente só muda de senha ou perfil com decisão explícita do operador
(decisão 7), o que também vale em `replace`.

## Consequências

- Migração entre máquinas vira procedimento de tela, documentado no RUNBOOK §10.
- Reimportar o mesmo pacote virou operação sem efeito, e não uma reescrita
  completa — o que torna seguro analisar, aplicar em partes e voltar depois.
- Segredo em repouso que não abre (banco de outra máquina, `APP_SECRET_KEY`
  trocado) vira string vazia no pacote em vez de derrubar a exportação: no
  estado em que a chave se perdeu, conseguir tirar o resto da configuração é
  mais valioso do que falhar inteiro.
- `update_broker` ganhou `clean_session`/`client_id` (não têm campo na tela) —
  sem eles a instalação restaurada assinaria o broker como cliente novo e
  perderia o backlog da sessão durável.
- Cada `.mwrbak` é um arquivo com credenciais reais circulando fora do sistema.
  A tela diz isso em texto; a alternativa (exportar mascarado) tornaria o
  arquivo inútil para provisionar telefone no destino.
