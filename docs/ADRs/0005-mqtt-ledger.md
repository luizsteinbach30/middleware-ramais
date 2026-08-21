# ADR-0005 — Ledger MQTT (registro auditável das mensagens do broker)

Data: 2026-08-19 · Status: aceito · Release: v2.8.0 (em desenvolvimento)

## Contexto

Um serviço publica o status dos ramais em um broker EMQX
(`v1/data/extenStatus/<ramal>`), mas **não registra os próprios envios**. Quando
surge a dúvida "essa mensagem foi publicada?", não existe onde olhar. A prova
vinha sendo obtida com o `log-emqx` (coletor em Go, `C:\Projetos\log-emqx`), que
grava uma árvore de pastas `logs/<tópico>/<AAAA-MM-DD>.jsonl` consultada por
linha de comando — inviável no dia a dia da operação.

O payload é o mesmo contrato do `/api/extenstatus` que o middleware já consome,
o que faz do broker uma fonte em tempo real (~5,4 s de atraso medido) do que
hoje chega por polling de no mínimo 60 s.

## Decisões

### 1. O registro é a mensagem crua, não o dado interpretado

`mqtt_messages` guarda `received_at` + `topic` + `payload` **verbatim** (base64
quando binário). `ramal` e `event_at` existem apenas para a busca; o valor
probatório está no trio acima. Payload que o parser não reconhece continua
gravado — a prova não depende de o middleware entender o conteúdo.

### 2. Sem árvore de arquivos: SQLite com retenção configurável

Requisito explícito do dono: nada de pastas se multiplicando em disco. O ledger
vive no mesmo banco do resto da aplicação, com retenção por dias
(`mqtt_message_retention_days`, padrão 7) e teto opcional por espaço
(`mqtt_message_max_mb`). Volume medido: ~90 msg/min, ~40 MB/dia de payload.

**Mensagem fixada como evidência (`pinned`) nunca é apagada** por retenção
nenhuma — nem por idade, nem por espaço. Um comprovante já usado em um chamado
não pode sumir sozinho.

### 3. Prova de cobertura em tabela própria

`mqtt_connection_events` registra `startup`/`connected`/`subscribed`/
`disconnected`/`error`/`stopped`. Sem esse histórico, a **ausência** de uma
mensagem não prova nada: pode ter sido o coletor fora do ar. `compute_coverage`
é conservador — um `startup` sem o `stopped` correspondente (processo morto)
marca o período anterior como *não comprovado*, em vez de assumir cobertura.

### 4. Sessão durável (`clean_session=False`, QoS 1, `client_id` estável)

Um reinício não pode virar buraco permanente no registro. Com sessão durável o
broker guarda as mensagens enquanto o serviço está parado e as entrega na volta
— verificado ao vivo: após 2 min parado, 86 mensagens chegaram com 30–82 s de
atraso, todas gravadas. O `client_id` é gerado uma vez e fica na linha do
broker; trocá-lo a cada boot jogaria fora a garantia.

### 5. Coletor no lifespan, não no APScheduler

O scheduler serve para trabalho periódico; isto é conexão persistente. O paho
roda em thread própria e só encosta em uma fila em memória; um worker no loop
drena e grava em lote (~1 s ou 500 mensagens por transação). A ingestão nunca
espera pelo disco, e a gravação nunca acontece uma linha por vez.

Fila com teto (10 mil): ao encher, descarta a mais antiga, conta e **mostra na
tela**. Comprovante que some sem aviso é pior que comprovante nenhum. Falha de
gravação devolve o lote para a fila em vez de descartá-lo.

### 6. Configuração por sonda, não por adivinhação

O operador digita só o endereço. Nada é deduzido do texto: cada candidato é
testado na rede e **só é aceito quando responde CONNACK de MQTT** — porta aberta
não prova nada. Porta digitada sempre é testada (nos quatro transportes); porta
ausente vira varredura de 1883/8883/8083/8084; TLS e websocket são detectados
por handshake, não pelo prefixo digitado.

Entre os endpoints que funcionam ganha o mais seguro (TLS antes de texto puro,
MQTT nativo antes de websocket), **exceto** quando o operador foi explícito em
esquema e porta — aí a vontade dele manda, inclusive para escolher texto puro
numa rede que bloqueia TLS. Isso nasceu de um caso real: colar a URL do painel
do EMQX (`http://host:18083`) levava a gravar um websocket sem criptografia só
porque o texto dizia "http". Hoje a sonda identifica o painel e sugere a 8883.

### 7. Certificado fixado em vez de "ignorar TLS"

O broker do cliente usa o certificado auto-assinado padrão do EMQX. Em vez de um
`tls_insecure` genérico (que aceita qualquer certificado, inclusive o de um
ataque), a tela mostra emissor, validade e impressão digital SHA-256 e o
operador confia **naquele** certificado: a impressão fica em
`mqtt_brokers.tls_fingerprint` e é conferida a cada conexão; se mudar, o coletor
recusa e registra o motivo.

### 8. Tópico é escolhido, não digitado — e o reconhecimento é pelo formato

O tópico varia por cliente e por versão do publicador. O teste de conexão escuta
o broker por alguns segundos e devolve os ramos que existem de fato, com
contagem, para o operador marcar. Como a ACL do broker pode negar `#` (é o caso
do EMQX do cliente), há uma escada de filtros (`#` → `v1/#` → `v1/data/#`) e a
tela diz qual foi recusado e onde a escuta aconteceu.

O reconhecimento de "status de ramal" é **pelo formato do payload**
(`{"retorno": {...}}` com `status`/`ramal`), não pelo nome do tópico: renomear o
tópico no publicador não quebra a integração.

## Estado do ramal: o que "registrado" quer dizer

O payload traz cinco status: `Disponivel`, `Indisponivel`, `Tocando`, `Ocupado`
e `Discando`. Eles respondem duas perguntas diferentes, e misturá-las causa
dano real:

- **"o ramal está registrado no PBX?"** → `Device.logical_status`. É o campo que
  `jobs/monitor_devices.py` usa como sinal de configuração perdida: telefone que
  responde ping **e** está `unavailable` tem a config reaplicada.
- **"o que o ramal está fazendo agora?"** → `Device.telephony_status` (novo).

Só quem está registrado toca ou conversa. Portanto **apenas `Indisponivel`
derruba `logical_status`**; `Tocando`, `Ocupado` e `Discando` continuam
`available`. O mapeamento anterior (`disponivel` → available, resto →
unavailable) fazia sentido enquanto o estado vinha só de uma coleta REST
esporádica, mas com o MQTT, que reflete o instante, ele reaplicaria configuração
em todo telefone que estivesse em ligação. A regra vive em
`domain/mqtt/parser.logical_from_status` e é usada pelas **duas** fontes — o
caminho REST (`upsert_from_uscall`) foi corrigido junto, porque o problema já
existia lá em menor escala.

Status que o publicador venha a inventar viram `desconhecido`: aparecem na tela
e **não** mexem no estado lógico — inventar `unavailable` a partir de algo que
não se entendeu seria pior do que não saber.

## Transições, não amostras

O publicador varre de 5 em 5 s e reenvia o estado atual de todo ramal (~90
msg/min na captura de referência). Gravar cada amostra em `extension_status_events`
repetiria a mesma linha milhares de vezes por dia sem acrescentar informação, e
a poda teria de ser agressiva justamente onde a linha do tempo interessa. Só
**mudanças** entram; a mensagem crua continua inteira no ledger e a transição
aponta para ela (`message_id`), então o comprovante nunca depende dessa
compressão.

**Medição de 2026-08-21, contra o broker do cliente (243 ramais):** ~99% das
mensagens viram transição — este publicador já fala só na mudança, então o filtro
quase não corta *aqui*. Ele continua no desenho por dois motivos que não dependem
de como o publicador se comporta: a sessão durável reentrega a fila acumulada
quando o serviço volta (e reentrega não pode virar transição nova), e um
publicador que varra periodicamente repetiria o mesmo estado indefinidamente.
Consequência prática para dimensionamento: **não** assumir que
`extension_status_events` é desprezível diante do ledger.

A chave do "mesmo estado" é `(status, numero, uniqueid, duracao)`. `duracao` (o
horário de início da chamada em curso, apesar do nome) entra porque duas
chamadas seguidas para o mesmo número, sem passar por `Disponivel`, teriam
status e número idênticos — e são duas chamadas.

O estado corrente por ramal vive em memória no coletor e é reidratado do banco
no boot; sem isso, o primeiro lote depois de um restart gravaria uma transição
falsa para cada ramal. Falha de gravação desfaz o cache, senão a transição
ficaria marcada como conhecida e nunca mais seria gravada.

## Consequências

- Nova dependência: `paho-mqtt` (Python puro — sem risco no PyInstaller onefile;
  entra em `hiddenimports` do `.spec`).
- Migration `0009_mqtt_ingest`: `mqtt_brokers`, `mqtt_messages`,
  `mqtt_connection_events`.
- Migration `0010_extension_status_events`: `extension_status_events` +
  `telephony_status`, `telephony_status_at`, `telephony_numero` e
  `status_source` em `devices`.
- A coleta REST (`collect_extensions`) **continua**: só ela traz IP/MAC, cria
  devices e sustenta ping, webhooks e o Configurador de Ramais. O payload MQTT
  não tem IP.
- O `log-emqx` deixa de ser necessário para operação (segue útil como bancada).

## Alternativas descartadas

- **Ler os arquivos `.jsonl` do log-emqx**: manteria a árvore de pastas que o
  dono quer eliminar e acoplaria o middleware a um binário externo em
  refatoração (hoje o fonte do log-emqx nem compila).
- **Só interpretar e guardar o status normalizado**: perderia o valor
  probatório, que depende do corpo como ele chegou.
- **QoS 0 / `clean_session=True`** (como está o `config.yaml` do log-emqx):
  simples, mas cada reinício abriria um buraco permanente no registro.
