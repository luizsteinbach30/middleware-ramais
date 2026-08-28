# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/) · SemVer.

## [2.10.1] — 2026-08-28

### Fixed
- **A tela Config padrão do Configurador não carregava — desde a v2.8.0.** Um
  `await` dentro de uma função não `async` (`collectFKs`), colado no lugar errado
  ao trazer a hora herdada do servidor (#45), é `SyntaxError`: o navegador
  **descarta o módulo inteiro** e nenhum handler é registrado. Os sintomas não
  pareciam sintaxe — *"ao clonar o ambiente vem tudo zerado"* (o `reload()` nunca
  rodava para preencher os campos) e *"ao salvar não redireciona, preciso sair e
  entrar de novo"* (o clique do Salvar nunca era ligado — ele não salvava nada).
  A chamada voltou para o fim do `reload()`, onde sempre pertenceu.
- **Nada no pipeline olhava para o JavaScript**, e é por isso que o erro
  sobreviveu a três releases: `ruff` e `mypy` só veem Python, e os testes
  exercitam a API, nunca a página. Agora todo módulo servido passa pelo parse do
  `node` (o mesmo motor do navegador) em `tests/unit/web/test_static_js_syntax.py`,
  e o CI ganhou o passo *Setup Node* para o teste nunca ser pulado lá.
- **Intelbras TIP 125i ficava preso na sessão SIP anterior — e o endpoint que
  resolve muda com o firmware.** Depois de gravar a config, o aparelho seguia
  registrado com a credencial antiga; o `notify.cgi` não põe a conta em vigor
  (no fw 4.3 ele nem sequer a derruba quando ela é desativada). O reinício certo
  depende da versão, e escolher só um deixa metade do parque parado:
  `restart_control_call.cgi` existe no **fw 5.0.x** e reinicia apenas a pilha de
  chamadas (registro volta em ~2 s, sem reboot); no **fw 4.3.x** ele responde
  **404** e o único caminho é `restart.cgi`, que reinicia o aparelho (~45 s fora
  do ar). O adapter tenta o leve e cai no pesado. Medido nos dois firmwares, com
  aparelho em campo: reboot detectado, telefone de volta e registrado em 44 s.
- ~~**Intelbras TIP 125i ficava preso na sessão SIP anterior.**~~ Gravar a config e
  notificar não refaz o registro: o aparelho seguia com a credencial antiga. E o
  caminho intuitivo não resolve — desativar a conta derruba o registro em 1 s,
  mas religar **não** o traz de volta. Quem religa é o
  `restart_control_call.cgi` (o mesmo que a interface do telefone usa), agora
  chamado ao fim de toda aplicação. Ele normalmente **não responde** — reinicia a
  pilha que está servindo a própria request —, então o timeout ali é o caminho
  feliz e não uma falha.
- **TIP 125i: `;` em qualquer valor quebrava a aplicação com o diagnóstico
  errado.** O CGI do aparelho separa os statements por `;` antes do SQL, então um
  `;` dentro de aspas parte o comando e o telefone responde **401** — que seria
  lido como "credencial recusada", mandando o pipeline tentar a outra senha e
  reportar um problema de autenticação inexistente. Valor com `;` ou caractere de
  controle agora é recusado **com o nome do campo**, em vez de limpado em
  silêncio: numa senha SIP, trocar o caractere sem avisar deixaria o ramal sem
  registrar e ninguém entenderia por quê.
- **TIP 125i: `affected: 0` passava por sucesso.** O contador conta linhas que
  casaram com o `WHERE` (gravar o mesmo valor ainda devolve 1), então 0 é sempre
  erro real — conta SIP fora do alcance do aparelho, ou um `nova_web_user` que
  não existe nele: a senha web "trocava" sem trocar.

## [2.10.0] — 2026-08-28

### Added
- **Intelbras TIP 125i no Configurador de Ramais** — sexto modelo e **terceira
  plataforma Intelbras**, sem nada em comum com a V-series (RapidLogic) nem com
  a linha S (GoAhead). Homologado ao vivo (fw 5.0.2): fingerprint, discover,
  geração, envio, backup e restauração conferidos contra o aparelho, que voltou
  ao estado original ao fim do teste.
  - O firmware **expõe o próprio banco**: a web UI monta SQL no navegador,
    codifica em Base64 e chama `GET /db.cgi?<base64>` (LuaSQL/SQLite);
    `notify.cgi?tables=…` é o "aplicar". Não há API por trás — o SQL é a API.
    Auth é HTTP Basic simples, sem login, sessão nem token CSRF.
  - Provisiona conta SIP (1 ou 2), hora (NTP + fuso), idioma do display,
    bloqueio de teclado, teclas programáveis e senha do admin web.
  - ⚠️ **Três armadilhas silenciosas, todas medidas na bancada e cobertas por
    teste:** (1) qualquer sobra depois do `;` final — nova linha, espaço,
    comentário `--` — faz o aparelho responder **HTTP 200 com corpo vazio** sem
    executar nada, então **corpo vazio agora é erro explícito** (senão a linha
    seria marcada como aplicada sem o telefone ter recebido nada); (2) Base64
    cru com `+`/`/` na query devolve **401**, e o sintoma não tem relação com a
    causa — mandamos percent-encodado; (3) `SYSTimeTimeZone` **não** é o offset
    puro: -180 é *Newfoundland* e Brasília é **-181**.
  - A whitelist "nunca tocar em rede" fica mais forte nesta plataforma: o
    `UPDATE` só toca as colunas que escrevemos (sem replay), e o SQL gerado
    ainda é re-parseado, abortando em qualquer verbo que não seja `UPDATE` ou
    par `tabela.coluna` fora da lista.
  - Sem *device actions* por ora: o `normalize` depende do volume máximo, que
    esta plataforma não expõe em tela web e não foi confirmado em hardware.

## [2.9.5] — 2026-08-25

### Added
- **Painel ao vivo deixou de ser ilha.** O cartão do ramal em `/mqtt-painel`
  mostrava estado e pouco mais; agora traz atalhos para **o telefone**
  (`/devices/{id}`, com IP, MAC, modelo e servidor USCall no tooltip) e para **o
  ambiente do Configurador** que provisiona a linha, além de um selo vermelho
  *config com erro* quando a última aplicação naquela linha falhou — que é o que
  responde "esse ramal está indisponível porque a config caiu?". Vínculo que não
  existe não vira link morto: some. O índice ramal → device/ambiente tem cache de
  30 s (`domain/mqtt/links.py`), porque a tela recarrega a cada 2,5 s e a
  instalação real tem ~800 ramais publicando.
- **Hora automática nos telefones Intelbras V-series.** O adapter passou a
  emitir a seção `<date>` (NTP e fuso), lida do **export de fábrica** do próprio
  aparelho (V3501 e V5501). Com HTEK e Yealink já cobertos, faltam agora só
  Intelbras S3002 e FlyingVoice P10, que dependem de bancada.
  ⚠️ `TimeZone` é id de tabela do firmware, não offset: nos dois exports vem
  `-12` para `UTC-3`. Só esse par é emitido; em qualquer outro fuso vai apenas o
  NTP, com aviso no log. E a mudança faz **toda linha Intelbras V aparecer uma
  vez como desatualizada** — é o esperado (o telefone precisa receber a hora),
  mas não é silencioso.
- **Backup e restauração** — tela nova em `/system/backup` (menu **Backup**),
  com dois níveis, porque migrar e recuperar são problemas diferentes:
  - **Pacote portável de configuração** (`.mwrbak`): configurações do sistema
    (retenções, ping, webhooks, auto-update, hora dos telefones), servidores
    USCall, brokers MQTT, **todos** os ambientes do Configurador com suas
    linhas, usuários (só o hash da senha) e o cadastro de devices — num arquivo
    só, cifrado com passphrase, para importar em **outra instalação**. Antes
    disso o export existia apenas por ambiente, um arquivo de cada vez, e a
    configuração do sistema não saía do banco de jeito nenhum.
    Importar **compara antes de aplicar**, item a item: o que já está igual é
    ignorado (não vira escrita nenhuma) e o que diverge aparece com os dois
    valores lado a lado para o operador escolher qual fica — por item ou de uma
    vez para o grupo inteiro. A aplicação roda em transação única, em modo
    *mesclar* (nada é apagado) ou *substituir*.

    Duas regras do desenho: valor de segredo (token, senha de broker, hash de
    senha) nunca aparece na comparação — a tela diz que difere e para por aí; e
    o padrão de **contas de acesso** é manter o que já existe, mesmo em
    *substituir*, porque o padrão errado ali tranca o operador para fora da
    própria instalação. Todos os outros grupos assumem o arquivo por padrão,
    que é o que "restaurar" significa.
  - **Snapshot do banco** (`.db.gz`, `VACUUM INTO` + gzip): cópia consistente de
    tudo, inclusive o histórico, para recuperar **esta** instalação. Medido no
    banco real: 58 MB viram 3,9 MB.
  - **Backup diário automático**, com horário no relógio do servidor, retenção
    por quantidade **e** por espaço, e — quando há passphrase salva — o pacote
    portável gerado junto. A pasta `backups/` existia desde a v2.0 e nunca
    recebia um arquivo.

  **Restaurar o banco não troca o arquivo a quente**: com o processo escrevendo
  nele, a substituição corromperia o banco e os jobs continuariam no antigo. O
  arquivo é validado (assinatura SQLite, `integrity_check`, tabelas obrigatórias
  e revisão de migration conhecida — backup de versão mais nova é recusado) e a
  troca acontece no próximo boot, antes de o engine abrir o banco. O banco
  substituído vira `pre-restore-*.db` e nunca é apagado pela poda.

  Os segredos (token do USCall, senha do broker, senhas SIP) saem em claro
  **dentro** do envelope cifrado: a cifra local deriva do `APP_SECRET_KEY` da
  máquina de origem e não teria como ser lida no destino. É por isso que a API
  recusa exportar sem passphrase.
- **Chamadas reconstruídas a partir do MQTT.** O PBX não publica chamadas —
  publica o estado de cada ramal. O middleware passa a deduzir a chamada dessa
  sequência: quem ligou, quem atendeu, quanto tocou e quanto durou. Tela nova
  **Chamadas** (menu Coletor MQTT) com filtro por período, ramal e número (ambos
  por trecho), direção e resultado, mais exportação em CSV com hora local e
  separador que o Excel em português entende.
  - **Resumo diário por ramal** (`extension_daily_stats`), que sobrevive à poda
    das transições e é o único histórico longo. Nele, **uma chamada com
    `uniqueid` conta uma vez por ramal**, mesmo que o PBX a tenha tocado várias:
    medido em produção, um grupo de captura gerou 11 pernas "perdida" para uma
    única ligação, e contar as pernas cruas inflaria o número quase três vezes.
  - Migration `0011_extension_calls`; retenção própria (90 dias para chamadas,
    365 para o resumo), configurável.

  **Duas premissas do ADR-0005 não sobreviveram aos dados reais** e foram
  corrigidas lá:
  - o campo `duracao` **não** é o início da chamada e não serve como chave —
    medido, ele muda em 98% das chamadas (1161 de 1183), porque marca o início
    do *estado atual*. O trecho passou a ser delimitado por estado (uma sequência
    ininterrupta de `tocando`/`discando`/`ocupado`), que funciona com e sem
    identificador;
  - o `uniqueid` **não** identifica um par de ramais: um grupo de captura toca
    vários com o mesmo id (medido: até 5).

  Armadilha tratada: `Indisponivel` ecoa o `uniqueid` da chamada anterior por
  minutos: herdá-lo grudaria pernas de chamadas diferentes e mostraria uma
  conversa que nunca existiu.
- **Medição-base de desempenho** (`scripts/perf_baseline.py` +
  `docs/design/PERF_BASELINE.md`) — primeira etapa da revisão de arquitetura, que
  era medir antes de mexer. Tamanho real por tabela e por índice, duração de cada
  job e tempo de servidor de cada tela, sobre o banco real (1.930 devices, 910
  ramais publicando). A ferramenta é versionada porque o valor de uma
  medição-base é poder repeti-la depois de cada mudança; ela roda sempre sobre
  uma cópia, com brokers e servidores desabilitados nela — senão a medição
  conecta no EMQX do cliente com o `client_id` da produção.

  Nada foi otimizado ainda, e o número desmentiu parte do que se suspeitava:
  nenhuma tela passa de **33 ms** de servidor, `/devices` pagina no banco e a
  reconstrução de chamadas custa 11–31 ms no caminho que roda de verdade. O que
  apareceu no lugar: **44% do arquivo do banco é espaço livre** que a poda nunca
  devolve (24,4 MB, recuperáveis com um `VACUUM` de 240 ms); o **maior custo do
  caminho de request é recompilação de template** — um `Jinja2Templates` novo por
  request custa ~8 ms em toda tela; e a requisição mais cara do sistema
  (`/api/mqtt/status`, 16 ms) é 2/3 varredura da tabela inteira do ledger para
  somar `payload_bytes`.

  Achado que atrapalha o próprio diagnóstico: **a duração de um job
  bem-sucedido não é gravada em lugar nenhum** — vai só para o stdout, e
  `system_logs` guarda apenas WARNING e ERROR. É por isso que `collect_extensions`
  e `monitor_devices`, os dois jobs mais suspeitos, seguem sem número.

### Changed
- **Toda tela ficou entre 36% e 65% mais rápida**, e nenhuma linha de regra de
  negócio mudou — as três causas eram desperdício, e vieram da medição-base:
  - **o ambiente Jinja era remontado a cada request.** Cada tela recompilava o
    template do zero, toda vez; era o maior custo do caminho de request, acima de
    qualquer consulta ao banco. Memorizado, o documento HTML caiu de 10–11 ms
    para **2 ms** em todas as telas e deixou de ser a requisição mais cara de
    qualquer uma. Editar template em desenvolvimento continua valendo sem
    reiniciar (o `auto_reload` do Jinja segue ligado), e a rede de segurança do
    `.exe` contra o sumiço da pasta de extração continua montada — agora sem
    depender da ordem entre a primeira tela e o `preload()`.
  - **a poda apagava e não devolvia o espaço.** O SQLite transforma em freelist a
    página que a poda esvazia, e o arquivo só cresce: no banco do cliente, 44%
    dele (24,4 de 55,4 MB) era espaço livre. A retenção agora compacta ao
    terminar — medido: **55,4 → 29,6 MB, 25,8 MB devolvidos em 253 ms**. Não roda
    todo dia: só quando há mais de 8 MB **e** mais de 20% do arquivo a recuperar,
    porque o `VACUUM` reescreve o arquivo inteiro e num dia de poda pequena não se
    paga. No dia em que compacta, o job vai de 18 ms para 235 ms.
  - **mostrar o tamanho do ledger custava varrer o ledger.** `SUM(payload_bytes)`
    lê a tabela inteira (15,6 MB, 10,8 ms) e a tela de Config → MQTT pedia isso a
    cada 5 s, a de Mensagens a cada 10 s, cada aba por sua conta — disputando o
    arquivo com a escrita do coletor. Agora tem TTL de 60 s. **A contagem de
    mensagens continua exata a cada chamada**, de propósito: custa 0 ms e é o
    número que o operador fica olhando enquanto configura o broker; congelá-la
    faria a tela parecer travada. Só o total de bytes, que é rótulo de ocupação,
    é que espera.

### Fixed
- **O `.exe` não cai mais quando o Windows esvazia a pasta de extração.**
  Relatado em campo: `TemplateNotFound: 'system_updates.html'` com o arquivo
  comprovadamente dentro do executável — o diretório temporário do PyInstaller
  tinha perdido o arquivo em tempo de execução (antivírus, limpeza de `%TEMP%`
  ou update parcial). Agora, no boot do `.exe`, templates e estáticos vão para
  memória e servem de fallback quando o disco falha; o disco continua sendo a
  fonte. Sem a metade dos estáticos a tela responderia sem CSS nem JS, que para
  o operador é a mesma coisa que estar fora.
  Junto vai o diagnóstico que faltava: uma sonda de 15 em 15 minutos grava
  `recursos_do_bundle_sumiram` **na hora** em que os arquivos somem, em vez de o
  fato aparecer só quando alguém abre a tela. A correção de raiz (empacotar em
  *onedir*) muda o formato de distribuição e continua em aberto.
- **Documentação do Action URI do Yealink era falsa.** O ADR-0004 e o guia de
  homologação afirmavam que o template provisiona a "Action URI Allow IP List";
  não provisiona, e nunca provisionou. O erro 403 do `normalize` agora diz onde
  liberar o IP (Features → Remote Control) em vez de mandar conferir a senha.
- **Exportar quebrava se um segredo em repouso não abrisse.** Banco vindo de
  outra máquina ou `APP_SECRET_KEY` trocado deixavam o token do USCall e a senha
  do broker ilegíveis, e a exportação inteira caía com erro 500 — justamente no
  estado em que mais se precisa tirar a configuração de dentro do sistema. Agora
  o segredo ilegível vira vazio e o resto do pacote sai normalmente.
- **Testes de chamadas quebravam sozinhos com o passar dos dias.** Eles
  gravavam as chamadas numa data fixa (o dia em que foram escritos) e
  consultavam a API com `last=24h`: três dias depois, a janela não alcançava
  mais o dado e quatro testes falhavam sem que nada tivesse mudado no código.
  Agora ancoram no relógio, como o teste de reprocessamento já fazia.

## [2.8.1] — 2026-08-21

### Fixed
- **A verificação de atualização falhava em toda máquina de cliente.** O log
  mostrava `CERTIFICATE_VERIFY_FAILED — unable to get local issuer certificate`
  a cada tentativa, e o efeito era pior do que parece: o app **nunca chegava a
  descobrir** que existia versão nova, então nenhuma correção chegava sozinha ao
  cliente. Causa: `desktop.py` verificava o release com `urllib.request.urlopen`
  **sem contexto TLS**, e sem contexto o Python cai no armazenamento de
  certificados do Windows, onde costuma faltar o emissor intermediário da cadeia
  da API do GitHub. Só o updater falhava porque só ele usa `urlopen` — todo o
  resto do app fala HTTPS por `httpx`, que já usa o `certifi`. O `cacert.pem`
  sempre esteve dentro do `.exe`; o updater é que não o usava. Agora a
  verificação usa a cadeia do `certifi` (que passou a ser dependência explícita),
  com a checagem de certificado **ligada** — o objetivo é usar a âncora certa,
  não afrouxar a validação. Sem `certifi` disponível, cai no contexto padrão em
  vez de derrubar o app.
- **Sessões penduradas no broker EMQX.** O coletor aparecia desconectando e
  abrindo conexão nova, deixando sessão fechada acumulada. Duas causas
  independentes:
  - **Encerramento sujo** — `disconnect()` do paho só *enfileira* o pacote; quem
    o escreve no socket é a thread de rede. O código chamava `loop_stop()` na
    sequência e matava essa thread antes do DISCONNECT sair, então o broker via a
    conexão cair de forma anormal. Com `clean_session=False` isso é caro: o EMQX
    mantém a sessão viva esperando o cliente voltar, guardando fila. Agora espera
    a confirmação, com teto de 1,5 s — broker mudo não pode travar a parada do
    serviço nem a tela de configuração.
  - **`client_id` volátil** — quando a linha do broker estava sem identificador,
    um novo era gerado a cada conexão e descartado, e cada conexão entrava no
    broker como um cliente diferente. Com sessão durável, o EMQX guardava uma
    sessão (com fila) para cada um. Agora é gerado uma vez e **gravado**; sessão
    durável exige identificador estável.
  - Além disso, **reconexão em ciclo passa a ser denunciada**. Em MQTT 3.1.1 o
    broker não diz por que derrubou — só fecha o socket —, então "rede ruim" e
    "duas instâncias disputando o mesmo `client_id`" chegam idênticos ao
    coletor. A única coisa que separa os dois é a frequência: 5 quedas em 2 min
    viram log de erro nomeando a causa provável, em vez de o operador só ver o
    coletor piscando.

  **Limite conhecido:** isto impede órfãos novos, mas **não remove as sessões que
  já estão penduradas** no broker, nem ajuda quando o processo é morto à força
  (aí o DISCONNECT não sai). Sessão MQTT 3.1.1 não expira sozinha — as antigas
  precisam ser descartadas no painel do EMQX.

### Added
- `docs/REQUISITOS.md` §15 — roadmap vivo das pendências em aberto, com o
  diagnóstico do que ficou de fora deste hotfix (inclusive o `TemplateNotFound`
  do `system_updates.html`, que **não** é omissão de empacotamento: o arquivo
  está no bundle da v2.7.2 e da v2.8.0, verificado no índice dos executáveis
  publicados).

## [2.8.0] — 2026-08-21

### Added
- **Coletor de mensagens MQTT (EMQX) — registro auditável dos envios.** O
  serviço que publica o status dos ramais não registra os próprios envios; o
  middleware passa a assinar o broker e a guardar cada mensagem como ela
  chegou, com a hora exata de recebimento. É de onde sai o comprovante de que
  a mensagem foi publicada, no tópico certo, na hora certa. Sem árvore de
  arquivos: tudo em SQLite, com retenção configurável (padrão 7 dias) e teto
  opcional por espaço — e **mensagem fixada como evidência nunca é apagada**.
  - **Configuração assistida:** o operador digita só o endereço
    (`emqx.exemplo.com`, `host:8883`, `ssl://`, `ws://`, `https://` ou até a
    URL do painel do EMQX). Porta, transporte e TLS são **descobertos testando
    a rede** — só vale endpoint que responde CONNACK de MQTT. Colar a URL do
    painel (`:18083`) é reconhecido e o sistema aponta a porta correta.
  - **Certificado auto-assinado** deixa de ser um "ignorar TLS": a tela mostra
    emissor, validade e impressão digital SHA-256, e o operador confia naquele
    certificado — que passa a ser conferido a cada conexão.
  - **Tópicos são escolhidos, não digitados:** o teste escuta o broker por
    alguns segundos e lista os ramos existentes com contagem. Quando a ACL do
    broker nega `#`, tenta ramos mais específicos e diz na tela o que foi
    recusado e onde escutou. O reconhecimento de status de ramal é pelo
    formato do payload, não pelo nome do tópico.
  - **Sessão durável** (`clean_session=False`, QoS 1, `client_id` estável): o
    broker guarda as mensagens enquanto o serviço está parado e as entrega na
    volta — reinício não vira buraco no registro.
  - **Prova de cobertura:** o histórico de conexão do coletor fica registrado,
    então "não há mensagem no período" passa a distinguir "ninguém publicou" de
    "não estávamos ouvindo". Reinício sem encerramento limpo marca o período
    como não comprovado, em vez de assumir cobertura.
  - **Comprovante em texto** por mensagem (`/api/mqtt/messages/{id}/comprovante`)
    com hora local e UTC, tópico, QoS, broker e o payload como recebido.
  - **Tela "Mensagens" (menu Coletor MQTT, abaixo do Configurador de Ramais)** —
    consulta por período (atalhos de 15 min a 7 dias ou intervalo à mão),
    tópico com curingas, ramal, texto no conteúdo e "só evidências"; modo ao
    vivo; detalhe com o payload cru e o formatado lado a lado; fixar evidência
    e baixar comprovante. A faixa de cobertura acompanha todo resultado, para
    que lista vazia signifique alguma coisa.
  - API `/api/mqtt/*` (brokers, discover, sniff, status, messages, coverage) e
    seção "Coletor de mensagens MQTT" na tela de Configuração.
  - Migration `0009_mqtt_ingest`; nova dependência `paho-mqtt`.
  - Ver `docs/ADRs/0005-mqtt-ledger.md`.
- **Estado dos ramais em tempo real a partir do MQTT.** O que chega do broker
  deixa de ser só linha no ledger e vira informação operacional: cada mudança de
  estado do ramal é normalizada em `extension_status_events` e refletida no
  telefone na hora, em vez de esperar o ciclo de coleta REST (mínimo 60 s).
  - **Só transições são gravadas** — repetição do mesmo estado não vira linha. A
    mensagem crua continua inteira no ledger e a transição aponta para ela, então
    o comprovante nunca se perde. Medido no broker do cliente (243 ramais): esse
    publicador já fala apenas na mudança, então ali o filtro quase não corta.
    Ele segue necessário por dois motivos independentes do publicador: a sessão
    durável reentrega a fila acumulada quando o serviço volta, e reentrega não
    pode virar transição nova; e publicador que varre periodicamente repetiria o
    mesmo estado sem parar.
  - **Tela "Painel ao vivo" (`/mqtt-painel`, menu Coletor MQTT).** Contadores por
    estado (clicáveis, filtram a grade), grade dos ramais com número da outra
    ponta e tempo no estado, fita das últimas transições e saúde da ingestão
    (msg/min, fila, descartes, atraso do PBX, última mensagem). Ramal que parou
    de ser publicado aparece apagado, com "sem msg há X" — grade toda verde de
    coletor parado seria pior que tela vazia.
  - Retenção própria das transições (`extension_event_retention_days`, padrão 7
    dias), configurável na tela.
  - Migration `0010_extension_status_events` (tabela + colunas
    `telephony_status`, `telephony_numero` e `status_source` em `devices`).
  - API `/api/mqtt/live`.

### Fixed
- **Ambiente Yealink quebrava no `.exe` (não no código-fonte).** O empacotador
  levava só os templates `*.xml` dos fabricantes, e o Yealink usa
  `yealink_template.cfg` (formato chave=valor, não XML). No executável o arquivo
  simplesmente não existia, então **qualquer** operação que renderize a config de
  um ambiente Yealink — abrir a planilha, salvar, aplicar — estourava
  `FileNotFoundError` e virava HTTP 500. Rodando do fonte funcionava, o que
  escondia o problema. O empacotador passa a casar por `*_template.*`, para o
  próximo fabricante que chegar com outra extensão não repetir a história.
- **Ramal em conversa não é mais tratado como configuração perdida.** O estado
  lógico do device (`logical_status`) era `available` só quando o PBX dizia
  `Disponivel` — qualquer outro status, inclusive `Tocando`, `Ocupado` e
  `Discando`, virava `unavailable`. Como `jobs/monitor_devices` usa exatamente
  "responde ping **e** está `unavailable`" como sinal de que a configuração do
  telefone sumiu, um ramal coletado no meio de uma ligação podia disparar
  reaplicação de configuração **durante a chamada**. Agora só `Indisponivel`
  derruba o estado lógico: quem toca ou fala está registrado. O estado de
  telefonia detalhado passou a viver em `Device.telephony_status`, separado. A
  correção vale para as duas fontes — MQTT e coleta REST.

## [2.7.2] — 2026-08-04

### Fixed
- **O `.exe` v2.7.0/v2.7.1 não iniciava em máquina nenhuma** — janela presa em
  "Iniciando...", porta 8080 nunca abria, zero mensagens (não era a porta
  ocupada; a v2.7.1 corrigiu um problema real, mas havia um segundo). Cadeia
  da falha:
  - O pipeline de release instalava dependências **sem versão pinada** e o
    build de 2026-08-03 levou o **structlog 26.1.0**, que quebra quando
    `sys.stdout` é `None` (`TypeError: cannot create weak reference to
    'NoneType'`) — exatamente o estado do exe `console=False` do PyInstaller.
  - O primeiro log do structlog acontece no **lifespan do uvicorn**
    (`app_starting`); o uvicorn trata a falha do lifespan com `sys.exit(3)` —
    e `SystemExit` **não é** `Exception`, então escapava do `except` da
    thread do servidor, que morria sem setar `error`.
  - Bônus: o `fileConfig` do `env.py` do Alembic **desligava os handlers de
    log do app no meio do boot**, então nem o `app.log` nem a aba de Log
    mostravam qualquer vestígio.

  Correções (defesa em camadas):
  - `desktop.py` garante `sys.stdout`/`sys.stderr` utilizáveis antes de
    importar a aplicação (imune à classe inteira de bugs "stream é None").
  - `ServerThread` agora envolve **todo** o boot (create_app + uvicorn.Config
    inclusos) em `try/except BaseException`, grava `logs/boot-crash.log` e a
    janela mostra o erro real em diálogo — nunca mais um "Erro" mudo.
  - `env.py` só chama `fileConfig` quando não há handlers instalados (CLI do
    alembic), com `disable_existing_loggers=False`.
  - Release passa a instalar com `-c packaging/constraints-build.txt`
    (dependências congeladas no conjunto validado) — atualizar dependência
    vira mudança deliberada e testada, não efeito colateral do calendário.
- **Planilha não abria em cliente sem internet** ("falha ao carregar:
  jspreadsheet is not defined"). O Jspreadsheet CE 4.15.0 e o jSuites 4.17.7
  eram carregados do CDN (jsdelivr) — dívida registrada no ADR 0002 — e em
  rede isolada os scripts nunca chegavam. As quatro dependências (JS + CSS)
  agora são **vendoradas em `/static/vendor/jspreadsheet/`**, como o Tailwind
  já era, e entram no `.exe`/tarball pelo empacotamento normal de `web/static`.
  Todos os `url()` dos CSS são `data:` URIs — o app não faz nenhuma
  requisição externa para renderizar a UI.

## [2.7.1] — 2026-08-03

### Fixed
- **O `.exe` não abria quando já havia uma instância na porta 8080.** Reportado
  em campo logo após a v2.7.0: o app "não iniciava" em servidor nenhum, sem
  mensagem alguma. O binário é empacotado com `console=False`, então o erro do
  uvicorn (`[Errno 10048] … apenas uma utilização de cada endereço de soquete`)
  ia para um stderr invisível e a janela simplesmente não aparecia. Acontecia
  quando a instância anterior seguia viva — processo órfão ou o serviço do
  Windows — disputando a mesma porta.
  - Agora o app **verifica a porta antes de subir** e mostra uma janela
    explicando: se quem está lá é o próprio middleware, oferece abrir o painel
    no navegador; se é outro programa, diz como descobrir qual.
  - A checagem **tenta de novo por alguns segundos** antes de desistir: logo
    após uma atualização o processo antigo pode estar encerrando, e uma corrida
    de 1 s não pode virar "o app não abre".
  - No encerramento o processo passa a sair **à força depois do shutdown
    limpo** — no empacotamento onefile, qualquer thread não-daemon sobrevivente
    mantinha o `.exe` vivo segurando a porta, que era a origem do órfão.

## [2.7.0] — 2026-08-03

> Migrations `0006`, `0007` e `0008` rodam juntas no upgrade. Instalações
> **≤ 2.6.0 não se auto-atualizam** (o updater antigo não fala com repo
> privado): é preciso **1 update manual** para a 2.7.0 em cada um dos 3 modos
> (.exe desktop, serviço Windows NSSM, Linux systemd) — ver `docs/INSTALACAO.md`.

### Fixed
- **Auto-update quebrado em campo** (repo GitHub privado): token fine-grained
  read-only embutido no build (`UPDATE_READ_TOKEN`, ofuscação cosmética —
  segurança real é escopo mínimo + rotação) com override por env
  `APP_UPDATE_TOKEN`; download passa a usar a **API URL do asset**
  (`assets[].url` + `Accept: application/octet-stream` + Bearer), já que
  `browser_download_url` não funciona em repo privado; validação por modo —
  `.exe` (frozen) exige `MiddlewareMonitor-*.exe` **com SHA256 conferido**,
  legacy mantém tarball+SHA256SUMS; default de `update_repo` corrigido.
- **Tela do ambiente travava após o 2º "Aplicar"**: ciclo de vida do polling
  refeito (`startPolling`/`stopPolling` por run, guard anti-sobreposição) e
  404 de run expirado encerra o acompanhamento com aviso em vez de poll
  infinito.
- **IPs/linhas fora de ordem**: a ordem canônica das linhas passa a ser a
  **ordem da planilha** (coluna `posicao`, migration `0006`, backfill por
  `created_at, id`). Tela, export XLSX/PDF e aplicação herdam a mesma ordem.

### Added
- **Múltiplos servidores USCall por instalação** (ADR-0003, migrations `0007`):
  tabela `uscall_servers` (token cifrado por linha), CRUD na API e na tela
  `/config` (cards com teste de conexão por servidor), coleta de todos os
  servidores habilitados em paralelo com **falha parcial segura** (um servidor
  fora não derruba os demais), verify de registro SIP consultando todos, e a
  config KV legada migrada automaticamente para o servidor "Principal".
- **Device actions — gerenciamento remoto dos telefones** (ADR-0004, migration
  `0008`): ação **"Normalizar telefone"** (volume no máximo + DND off — desfaz
  mute/DND ativado por operador) por linha (menu `⋮` na planilha) e **em massa**
  (botão "Normalizar telefones", que respeita a seleção da coluna `✓`: com
  linhas marcadas normaliza só as selecionadas) com progresso ao vivo
  (`GET /action-runs/{id}/live`). Homologado ao vivo em **4 dos 5 adapters**:
  **Yealink T31G** (Action URI), **FlyingVoice P10** (form-replay; reinicia ao
  mudar DND), **HTEK UC902G** (P-codes de volume e DND; reinicia sempre) e
  **Intelbras V-series** (V3001/V3101/V3501/V5501 — Action URI `DNDOff` para o
  estado de runtime **+** `sysConf` parcial para persistir DND, `MuteRinging`
  e volumes de saída; sem reboot). O **Intelbras S3002**
  (adapter distinto, firmware GoAhead) ficou de fora por falta de unidade de
  lab e segue oculto por capability. Matriz completa em
  `docs/design/DEVICE_ACTIONS_HOMOLOGACAO.md`. Auditoria completa em `device_action_events`
  (1 evento por telefone/ação, sucesso e erro). Catálogo prevê `set_ip` com
  confirmação digitada do IP atual (nenhum vendor homologado ainda).
- **Renomear ambiente pela UI** (lápis no título; o backend já existia).
- **Apagar ambientes selecionados** na lista: botão que aparece com a seleção
  ativa, modal com a lista do que será apagado e confirmação digitada.

### Changed
- **Busca de ambientes restrita ao nome** — antes o campo casava também ramal,
  IP, MAC e user auth internos da planilha, trazendo ambientes "errados".
  O modelo do telefone segue com filtro dedicado (dropdown).
- **Webhook**: cada item de `extensions`/`devices` ganha a chave **aditiva**
  `"uscall_server"` (nome do servidor de origem). Receptores que ignoram
  chaves extras não são afetados.

## [2.6.0] — 2026-06-03

### Added
- **Novos fabricantes no Configurador de Ramais:**
  - **Yealink (T3x/T4x)** — engenharia validada contra T31G (fw 124.86.104.1).
    HTTPS com cert self-signed (`verify=False`), login com senha cifrada em
    **RSA no cliente** (PKCS#1 v1.5), token CSRF `g_strToken` e envio de config
    via import `localcfg`.
  - **Intelbras S3002 (linha S / firmware GoAhead)** — adapter próprio,
    **distinto do V-series** (RapidLogic). Login plaintext com sessão por IP,
    discover via `/home.asp` e envio de conta SIP por *replay* de formulário.
    **Paridade de configuração com o V5501:** conta SIP, troca de credencial web,
    teclas programáveis e **bloqueio de teclado/menu** (`SysConfig.asp` →
    `SaveSysCfg`, escopado por `currentPage=Lockkeys_child`, preservando os
    números de emergência via replay). `keylock_enable` 0/1/2 é mapeado para o
    `LockKeys` do S3002 e o PIN (`keylock_password`) exige 4–15 dígitos.
    Validado em lab (fw V1.7.0.010412359).
- **Catálogo de softkeys por fabricante** (`/config` → teclas programáveis): a
  UI passa a listar os **tipos de tecla nativos do fabricante** do modelo
  selecionado (em vez de uma lista genérica), expondo só os tipos com encoding
  confirmado. No FlyingVoice isso inclui **Menu**, DND, Histórico, Diretório,
  LDAP, Status, Paging, alternância de conta, etc.
- **Seleção da conta SIP (1 ou 2) por fabricante.** O seletor de conta só
  habilita "Conta 2" onde o mapeamento está confirmado (Yealink); demais
  fabricantes seguem aplicando na conta 1.
- **Verificação de registro SIP pós-aplicação** (opt-in `verificar_registro_sip`):
  depois de aplicar e o telefone reiniciar, confirma via USCall se o ramal
  voltou a **registrar no PBX**. Consulta em lote (1 request cobre todo o run)
  ou por ramal. Snapshot do registro gravado no relatório de execução
  (migration `0005`).
- **Export/import de ambientes cifrado por passphrase** (`export_crypto`):
  envelope portátil (PBKDF2-HMAC-SHA256 + Fernet) que pode ser importado em
  outra instalação desde que se conheça a passphrase — diferente do `SecretBox`,
  atrelado à instalação.
- **Monitor de ping ao vivo na tela Ambiente** e **coleta de MAC via ARP**.
- **Duplicar ambiente** (lista e detalhe): cria um novo ambiente copiando o
  modelo do telefone e **toda a config padrão** (servidor SIP, credenciais,
  teclas programáveis, idioma, etc.). Os **ramais não** são copiados — o novo
  ambiente nasce vazio para receber outros telefones.

### Fixed
- **FlyingVoice — conta da softkey de discagem rápida (off-by-one).** O campo
  `line` da softkey é 0-based no aparelho (0 = Conta 1), mas a tela expõe
  "Account" 1-based; o adapter enviava o valor cru, então **"Account 1" virava
  Conta 2** no telefone (e Account 0 virava Conta 1). Agora a conta é
  convertida (`account − 1`, com clamp ≥ 0): **Account 1 → Conta 1**,
  **Account 2 → Conta 2**.

## [2.5.0] — 2026-05-25

### Added
- **Ações em massa na tela Devices.** Seleção múltipla (checkbox por linha +
  "selecionar página") com barra de ações: **apagar** devices (preserva as
  linhas de ambiente, apenas desvincula; remove os pings), **adicionar a um
  ambiente existente** e **criar um novo ambiente** a partir dos devices
  selecionados. Ao virar linha, só os campos conhecidos são preenchidos (IP e
  ramal); devices já vinculados ou com IP duplicado no ambiente são pulados.
- **Novos filtros em Devices:** por **ambiente** (campo com busca/typeahead,
  feito para clientes com muitos ambientes), **faixa de IP** (de/até) e **faixa
  de ramal** (de/até).
- **Exportação de ambientes em XLSX e PDF** (individual ou vários selecionados),
  com modelo do telefone, configurações do ambiente e a tabela de ramais.
  Senhas saem mascaradas. A logo da empresa entra no cabeçalho do PDF.
- **Relatório de execução com snapshot real** (nova tabela
  `extension_apply_run_lines`, migration `0004`): cada execução grava, por
  ramal impactado, o **status antes → depois**, quem aplicou e o erro. Runs
  antigos caem num fallback que mostra o estado atual.
- **Fallback de credencial no provisionamento.** Se o telefone recusa a
  credencial atual (senha já trocada), o sistema tenta automaticamente a
  credencial "nova" do ambiente (`nova_web_*`). Exceção semântica
  `VendorAuthError` unificada entre HTEK/Intelbras/FlyingVoice.
- **Identidade visual configurável** (`/config` → "Identidade visual"): upload
  de **logo** e **favicon**, exibidos na sidebar, na tela de login, na aba do
  navegador e no cabeçalho dos relatórios PDF.

### Changed
- **Watcher de auto-reaplicação** agora também dispara quando o ramal está
  `unavailable` no PBX **mesmo sem queda de ICMP** (config alterada sem o
  telefone cair) e **desiste** após falhar com as duas credenciais — só volta a
  tentar quando o telefone reregistra ou após aplicação manual. Evita o loop de
  reaplicação a cada ciclo.
- **Horários** na lista de ambientes e nos relatórios passam a ser exibidos no
  **fuso local** do navegador (antes mostravam UTC cru).
- **Assets estáticos** servidos com `Cache-Control: no-cache` (revalidação),
  evitando que o navegador use JS/CSS antigos após uma atualização.

### Fixed
- **Status dos ramais nos cards de ambientes.** O resumo comparava
  `ultimo_status` com `"applied"/"error"`, mas o valor gravado é `"ok"/"erro"`,
  então ramais aplicados apareciam eternamente como "pendentes".

## [2.4.0] — 2026-05-23

### Added
- **Modelo FlyingVoice P10 homologado** no Configurador de Ramais
  (`FlyingVoice P10` em `PHONE_MODELS`). Novo vendor `flyingvoice` registrado
  e roteado por modelo. Validado ao vivo (firmware V0.11.6):
  - **Registro SIP** (conta 1) via `/goform/setSip_account` — ramal fica
    `Registered`, sem reboot, com a rede do aparelho intacta.
  - **Softkeys** (todas) via `/goform/saveMultiFunc` — gerência completa das
    funções (Menu, DND, Discagem Rápida, Histórico, Diretório, etc.),
    preservando as teclas não alteradas.
  - Particularidades do firmware tratadas: POST exige **HTTP/1.0** (HTTP/1.1 é
    descartado), **replay do formulário inteiro**, e senha SIP em texto puro.
  - Regra **nunca-tocar-em-rede** garantida por whitelist + testes (a página da
    conta ainda traz `DBID_DNSSRV_DOMAIN`/portas, que nunca sobrescrevemos).

### Pendente
- Troca de credencial web do FlyingVoice (`nova_web_*` → `/goform/setSysAdm`)
  está implementada porém **não validada em hardware** (o `setSysAdm` não
  respondeu em teste e a credencial não mudou). Só é acionada se o ambiente
  definir `nova_web_password`; o provisionamento SIP normal não a usa.

## [2.3.1] — 2026-05-23

### Fixed
- **`.exe` quebrava ao salvar a planilha / calcular status (HTTP 500)** — os
  templates de configuração dos vendors (`intelbras_template.xml`,
  `htek_template.xml`) são lidos em runtime via `Path(__file__).parent`, mas
  o `MiddlewareMonitor.spec` não os empacotava (PyInstaller só inclui `.py`).
  No app congelado a leitura disparava `FileNotFoundError` em qualquer render
  de config — salvar planilha, calcular hash/status, aplicar. Agora o spec
  empacota os `*.xml` dos vendors junto ao pacote. A vinculação automática
  por IP já funcionava no banco; o 500 só impedia a tela de exibir o vínculo.

## [2.3.0] — 2026-05-23

### Added
- **Vinculação Device ↔ ExtensionLine** — cada ramal cadastrado no
  Configurador de Ramais pode ser associado a um `Device` descoberto via
  USCall. A vinculação é automática quando IP da linha bate com IP do
  device, e pode ser feita/desfeita manualmente.
  - `extension_lines.device_id` (FK nullable, `SET NULL` em delete).
  - Migration `0003_device_line_link` com backfill por IP.
  - Auto-link nos 3 momentos: (1) toda vez que `upsert_from_uscall` cria
    ou atualiza um Device; (2) toda vez que a planilha de um ambiente é
    salva; (3) sob demanda via botão "Vincular por IP agora" em `/config`.
- **Watcher de auto-reaplicação** — quando um device vinculado faz a
  transição `offline → online` no ping (ICMP), o sistema reaplica a
  config no telefone automaticamente. Regras:
  - **PBX-aware**: só age se `device.logical_status='unavailable'` no
    USCall (PBX não vê o ramal). Se PBX vê o ramal como `available`, o
    telefone está provisionado corretamente e nada é feito.
  - **Debounce por linha**: configurável (default 60 min), evita storm
    em redes instáveis.
  - **Toggle global**: `auto_reapply_on_recovery` (default `false`).
  - Tudo registrado em nova tabela `line_reapply_events` com motivo
    (`recovery` | `manual_device_page`), status e referência ao
    `ExtensionApplyRun` gerado.
- **Apply ad-hoc na tela do device** — botão "Importar config" em
  `/devices/{id}` dispara `apply_single_line` imediatamente (ignora
  toggle global e debounce). Operador registrado como o usuário logado.
- **Propagação automática de IP** — quando o USCall traz o mesmo ramal
  com IP diferente (DHCP refresh, troca de rede), o `device.ip` E o
  `extension_lines.ip` das linhas vinculadas são atualizados.
- **Tela `/devices/{id}` ganha bloco "Configurador de ramais"** — mostra
  ambiente vinculado, ramal, status da última config, hash, histórico de
  reapply events e botões "Importar config", "Vincular linha",
  "Desvincular".
- **Modal de vinculação manual em 2 passos**:
  - Passo 1: lista ambientes com linhas órfãs, ordenando os com IP
    casado para o topo (badge verde **IP bate**).
  - Passo 2: o sistema sugere automaticamente a linha por (a) IP igual,
    (b) ramal igual ao nome do device, (c) única linha órfã do ambiente.
    Fallback: lista as linhas órfãs para escolha manual.
- **Planilha do ambiente ganha coluna `Device`** — exibe nome do device
  com pill de status de rede (🟢/🔴/⚪). Click abre popover com
  "Ver telefone →" (link para `/devices/{id}` em nova aba) e
  "Desvincular".
- **Lista `/devices` ganha coluna `Vínculo`** — link para o ambiente +
  ramal e nome visível. Endpoint `GET /api/devices` retorna info do
  vínculo via batch query (evita N+1).
- **Cards `/extension-configurator/environments` mostram contador** —
  pill "X/Y devices vinculados" (verde 100%, azul ≥50%, amarelo >0,
  cinza nenhum).
- **Configurações `/config`** ganha bloco "Auto-reaplicação de configs"
  com toggle + input de debounce + botão **"Vincular por IP agora"**
  (roda `auto_link_lines_by_ip` sob demanda).
- **Endpoints novos**:
  - `GET /api/devices/{id}/extension-line` — linha vinculada (ou null).
  - `GET /api/devices/{id}/link-environments` — ambientes candidatos.
  - `GET /api/devices/{id}/link-suggestion?environment_id=X` — sugestão.
  - `GET /api/devices/{id}/available-lines?environment_id=X` — linhas órfãs.
  - `POST /api/devices/{id}/link` `{line_id}` — vincula manualmente.
  - `DELETE /api/devices/{id}/link` — desvincula.
  - `POST /api/devices/{id}/apply-config` — apply ad-hoc.
  - `GET /api/devices/{id}/reapply-events` — histórico.
  - `POST /api/devices/auto-link` — auto-link em massa por IP.

### Changed
- `Device` ganhou coluna `network_status_prev` (para detectar transição
  `offline → online` com segurança após o `record_ping`).
- `ExtensionLine` ganhou `device_id` (FK) e relacionamento
  `reapply_events` (cascade).
- `record_ping` agora persiste o status anterior antes de atualizar.
- `save_lines` no Configurador zera `device_id` quando o IP da linha
  muda e não bate com o device atual, depois roda auto-link para
  revincular se houver match.
- `GET /api/devices` e `GET /api/extension-configurator/environments/{id}`
  passam a expor `device_id`/`device_name`/`device_ip`/
  `device_network_status` por linha (e `extension_environment_id`/
  `nome`/`extension_line_*` por device).

## [2.2.3] — 2026-05-22

### Added
- **Filtros na lista de ambientes** — barra acima do grid com:
  - Busca livre que cobre nome do ambiente, modelo, IP, ramal, nome
    visível, user auth, número abreviado, MAC e modelo aplicado (múltiplos
    termos = AND).
  - Select de modelo (só lista modelos em uso; demais aparecem como
    *(não usado)* desabilitados).
  - Select de status agregado: `✓ Todos aplicados`, `○ Tem pendentes`,
    `⚠ Tem erros`, `∅ Sem ramais`.
  - Botão **Limpar**.
  - Filtros persistidos em `localStorage`.
- **Status pill em cada card** — badge colorido (verde/amarelo/vermelho/
  cinza) com a saúde agregada do ambiente + contagem entre parênteses
  quando relevante (ex: `⚠ erros (3)`).
- **Preview esmaecido durante drag-fill** — enquanto o usuário arrasta o
  canto inferior direito da seleção, ghosts cinza-translúcidos aparecem
  nas células-destino mostrando o valor que será gravado ao soltar.
  Suporta seleção 2D (multi-coluna e/ou multi-linha) com sequência por
  coluna baseada no padrão da última linha da fonte.
- **Autosave 1200 ms** — após a última edição na planilha, o backend é
  chamado sozinho com indicador visual no header:
  `• Edição não salva` (âmbar) → `↻ Salvando…` (azul) → `✓ Salvo` (verde).
- **Máscara IPv4** no campo IP da planilha — auto-`.` a cada 3 dígitos,
  apenas dígitos e ponto, máximo 15 caracteres (`inputmode=decimal`).

### Changed
- **Ordem das colunas da planilha**: IP, Nome visível, Ramal, User auth,
  Senha SIP, Servidor SIP, Nº abreviado (antes: Nome visível primeiro,
  depois IP). Todos como `type:'text'` para preservar zeros à esquerda
  (`00001` permanece `00001`, não vira `1`).
- **Contador inteligente** no header da lista: passa de `12 ambientes`
  para `4 de 12 ambientes` quando há filtros aplicados.
- Backend `_env_summary` passa a receber `list[ExtensionLine]` (não só
  `line_count`) e devolve `status_resumo` + `searchable` (string
  lowercase pré-concatenada) — base para a busca rica client-side.

### Fixed
- **Paste em range** deixa de virar incremento numérico — copiar `3`
  e colar em 5 linhas agora resulta em `3,3,3,3,3` (e não `3,4,5,6,7`).
  Implementado via flag `_pasteInProgress` setada em `onbeforepaste` e
  consumida no `onafterchanges`.
- **Drag-fill numérico**: arrastar `3` agora gera `4, 5, 6, 7…`
  (off-by-one anterior gerava `3, 4, 5…`, repetindo o valor original).
- **Drag-fill com fonte multi-coluna** — cada coluna do range-fonte
  ganha sequência própria; valor escrito é substituído via
  `onbeforechange`, garantindo consistência entre o preview e o commit.

## [2.2.2] — 2026-05-22

### Added
- **Apagar ambientes** — cada card no `/extension-configurator/environments`
  ganha ícone de lixeira no canto superior direito (revelado no hover/focus).
  Abre modal de confirmação em vermelho que exige digitar o **nome exato**
  do ambiente para habilitar o botão Apagar (padrão GitHub/Vercel — protege
  contra clique acidental). Cascade no DB já garantia limpeza de linhas e
  histórico de execuções.

### Changed
- **Toast centralizado no topo** — substitui o antigo no canto direito.
  Posicionado no centro horizontal/topo (`z-index: 9999`), com slide-down
  elástico na entrada e slide-up + fade na saída. Backdrop-blur, ring
  interno colorido por tom (success/error/info/warn), ícones SVG, sombra
  dupla. Auto-dismiss 3.2s, clique dismissa instantâneo, stack vertical
  para múltiplos. API pública inalterada (`toast.success/error/info/warn`)
  — todas as telas do app (Webhooks, Devices, Coletas, Config, etc.)
  ganham o novo visual sem alteração de código.
- Card do ambiente passa a exibir contador como **"N Ramais"** (fixo, sem
  variação de singular/plural).

### Fixed
- **Pluralização "ramalis"** — bug pré-existente: o padrão
  `${n} ramal${n === 1 ? '' : 'is'}` produzia *"16 ramalis"* em vez de
  *"16 ramais"*. Corrigido em 3 lugares (subtítulo da planilha, toast
  de aplicar, modal de delete).

## [2.2.1] — 2026-05-22

### Fixed
- **Bug crítico da planilha** — CDN do Jspreadsheet apontava para
  `dist/jspreadsheet.js` (404), trocado para `dist/index.min.js`. A planilha
  do Configurador de Ramais não abria.
- **HTEK URL-decode quirk** — firmware faz URL-decode no XML antes de gravar
  nos P-codes. Senhas com `%`, `&`, `<`, `>` viravam lixo no aparelho.
  Nova função `_htek_text()` aplica `urllib.parse.quote` + `xml_escape` em
  todo campo de texto (P3 DispalyName, P34 senha SIP, P35 SipUserId, P36
  AuthenticateID, P47 Sipserver, P30 NTP, P2 AdminPassword, P8681 LogUser,
  softkey value/label).
- **HTEK softkey `account=0`** — força Account1 em todas as softkeys.
  Valores diferentes apontavam para perfil inexistente e a tecla não
  discava.
- **Intelbras escape de senha** — `_xml_escape_password()` escapa aspas
  (`"` → `&quot;`, `'` → `&apos;`) em `RegisterPswd` e `web/account/Password`
  para evitar corrupção do valor armazenado.
- **Status "aplicado" após apply** — `_apply_row` recalcula o hash com
  env+linha frescos do DB após send com sucesso, eliminando divergências
  entre o hash salvo e o hash recomputado no reload (que causavam
  "outdated" falso na UI).
- **Toasts no Configurador de Ramais** — bug pré-existente: as páginas
  chamavam `toast({tone, text})` mas a API exportada é
  `toast.success/error/info`. Nenhum toast funcionava no módulo.

### Added
- **Tela de detalhe do relatório** — nova rota
  `/extension-configurator/runs/{id}` + endpoint
  `GET /api/extension-configurator/runs/{id}/detail`. Cards com Total/OK/
  Falha/Duração/Operador + tabela linha-a-linha (IP, ramal, nome, status
  com badge, modelo, MAC, última aplicação, erro). Listagem de relatórios
  ganhou link **abrir →** e linha clicável.
- **Editor de Function Keys (HTEK) / DSS Keys (Intelbras)** na tela
  Config padrão: tecla (LineKey1..4), tipo (Desabilitada/Linha SIP/
  Discagem rápida/BLF), label, account, valor (fixo ou da coluna da
  planilha). Para HTEK o campo Account fica oculto e força `0` no save.
- **Modelos `HTEK UC912` e `HTEK UC924`** adicionados a `PHONE_MODELS`.
- **Smart autofill numérico** na planilha — detecta prefixo + sufixo
  numérico (`RAMAL01` → `RAMAL02`, `192.168.0.10` → `192.168.0.11`)
  quando o usuário arrasta o canto de uma seleção, complementando o
  autofill nativo do Jspreadsheet que só funciona com número puro.
- **Coluna `✓` de seleção** + botões **marcar todos / desmarcar / só erros
  ou pendentes** + endpoint `/apply` aceita `selected_ids` no body para
  reaplicar só linhas específicas (útil quando alguns aparelhos estavam
  offline e o operador volta depois).
- **Pills de status** (aplicado / desatualizado / pendente / erro) com
  contadores no topo da planilha.
- **Aviso de senha SIP problemática para HTEK** antes de aplicar — alerta
  quando a senha tem mais de 25 caracteres ou contém chars fora do safe
  charset conhecido do firmware (`A-Za-z0-9!#%*+,-./:=?@_~`).
- **Toggle "Forçar reaplicação"** — quando ativo, reaplica em todos os
  aparelhos com IP ignorando o status atual.
- **Colunas extras na planilha**: Modelo, MAC, Última aplicação, Erro
  (preenchidas durante o polling do apply em tempo real).
- **Rolling delay** configurável (default 1s) entre disparos para evitar
  pico de rede em ambientes grandes.

### Changed
- **Config padrão**: campo *Servidor SIP* removido da tela — o valor agora
  vem exclusivamente da coluna `Servidor SIP` da planilha (por linha).
- **Pós-criação de ambiente**: redireciona para `/config` em vez de
  `/detail` para o usuário ajustar credencial e function keys antes de
  começar a mexer na planilha.
- **Polling de apply**: stages intermediários (`ping`/`send`) mostram
  *"aplicando…"* em vez de "desatualizado" para evitar flicker de status
  incorreto durante a execução.
- **Visual da planilha**: wrapper com card + sombra, header sticky
  uppercase, hover de linha, readonly diferenciado, foco azul no editor,
  scrollbar discreta, context menu arredondado.
- **Botões "marcar todos / desmarcar / só erros"** viraram um grupo
  segmentado pill com ícones e cores por ação. *"Forçar reaplicação"*
  virou toggle switch que destaca em azul quando ativo.
- **Botão "voltar"** padronizado como pill com chevron (consistente em
  detail/config/run_detail).
- **Layout Config padrão**: `<fieldset>/<legend>` trocados por
  `<div>/<h3>` — o reset CSS do Tailwind estava deslocando os títulos
  para fora das bordas dos cards.

### Tests
- 93/93 verdes; ruff clean; mypy --strict OK no código tocado.

## [2.2.0] — 2026-05-21

### Added
- **Configurador de Ramais** — módulo novo que migra o projeto standalone
  `autocfg-ramais` para dentro do middleware. Permite cadastrar ambientes
  (cada um com um modelo de telefone), preencher uma planilha de ramais e
  aplicar a configuração em massa nos aparelhos via web GUI deles.
  - Adapters validados em hardware lab: **HTEK UC902G** (HanLong, Basic/Digest
    auto) e **Intelbras V-series** (V3001/V3101/V3501/V5501, auth
    `md5(user:pwd:nonce)`, HTTP/1.0 forçado para contornar bug de chunked).
  - **Whitelist anti-rede inviolável**: nenhum adapter pode emitir tags ou
    P-codes de IP/máscara/gateway/DNS/VLAN/VPN/QoS/Wi-Fi. Configs parciais
    preservam tudo que não é enviado.
  - Defaults universais Intelbras: `EnableKeyLock=2`, `KeyLockTimeout=30s`
    (bloqueio do menu habilitado por padrão).
  - DSS Memory Key com subtype Speed Dial: `<Value>{numero}@{account}/f</Value>`
    — descoberto via engenharia reversa de backup XML real.
  - 3 tabelas novas: `extension_environments`, `extension_lines`,
    `extension_apply_runs` (migration alembic `0002_extension_configurator`,
    reversível).
  - Sidebar ganhou seção **Configurador de Ramais** com 2 entries:
    **Ambientes** + **Relatórios**.
  - Planilha estilo Excel: Jspreadsheet CE 4.15 (via CDN — vendoring offline
    em release futura).
  - Pipeline minimalista (ICMP ping opcional → send) com tracking ao vivo
    do progresso (polling 1.5s) e rolling delay (default 1s) entre disparos
    para evitar pico de rede.
  - Endpoints sob `/api/extension-configurator/` com auth obrigatória, CSRF
    e `require_admin` em mutações.
  - 47 testes novos (repository, service, vendors HTEK/Intelbras, API,
    smoke web).
- **ADR-0002** documentando a decisão arquitetural do Configurador de Ramais.

### Changed
- `VendorAdapter.send_config` ganha kwarg `fmt: str = "xml"` (HTEK também
  aceita `bin`).

### Notes
- Após o upgrade, rodar `alembic upgrade head` (cria as 3 tabelas novas).
- Projeto `autocfg-ramais` (POC standalone) foi marcado como arquivado;
  o código vivo do módulo agora é parte deste repositório.

## [2.1.5] — 2026-05-18

### Fixed
- **Formato do payload de webhook alinhado ao que o receptor aceita.**
  O evento `devices` enviava `data` como objeto
  (`{online, offline, items}`); a aplicação que recebe esperava um
  array plano. Agora `data` é o array diretamente, sem o invólucro de
  contadores — os totais online/offline seguem apenas na linha de log
  `monitor_ok`.
- **Campos de cada item renomeados** para o contrato do receptor:
  `ramal` → `name`, `network` → `status`, `latency_ms` → `latency`,
  e os novos campos `logical_status` (status lógico do USCall) e
  `last_ping` (último ping em hora local) passam a ser enviados.
- **`timestamp` do envelope em hora local** no formato
  `YYYY-MM-DD HH:MM:SS` (sem `T`, sem `Z`), em vez de ISO-8601 UTC.
  Os timestamps gravados em `webhook_events` continuam em UTC.
- O payload de **teste** (`/webhooks/test/...`) também passou a ser um
  array, com a mesma forma de um dispositivo real, para o receptor
  poder usar o mesmo parser em eventos de teste.

### Tests
- Nova suíte `tests/unit/test_webhook_payload.py` fixa o contrato:
  `data` é sempre array, `timestamp` é hora local simples, e o envio
  de um array de `devices` é aceito com `202`/`200`.

## [2.1.4] — 2026-05-12

### Added
- **Acesso via rede e port-forward.** O `.exe` agora liga em
  `0.0.0.0:8080` por padrão em vez de só `127.0.0.1`, então qualquer
  estação da mesma LAN abre `http://<ip-do-servidor>:8080/`. O IP
  detectado da interface principal aparece ao lado de **LAN:** na
  janela do app e também na aba **Sobre**, clicável e copiável.
  Operadores que ainda querem loopback-only podem definir
  `APP_HOST=127.0.0.1` no ambiente.
- **Exportar payload de webhook.** Cada linha em
  `/webhook-events` ganhou um ícone de download que baixa o JSON
  completo (`webhook-<tipo>-<id>.json`). O modal de visualização
  também passou a ter os botões **Copiar** e **Baixar JSON** no
  cabeçalho, com `event_type` agora visível no meta.
- Manual atualizado com o comando `New-NetFirewallRule` para liberar
  a porta 8080/TCP no Windows e a recomendação de só expor o painel
  pela internet atrás de VPN / reverse-proxy com TLS.

### Security (acompanhando a exposição em LAN)
- **`/api/docs` e `/api/openapi.json` agora vêm desligados por padrão.**
  Em `0.0.0.0` qualquer host da LAN poderia enumerar endpoints e
  schemas sem autenticação. Para reativar em desenvolvimento defina
  `APP_EXPOSE_DOCS=1`.
- **Aviso visual no app** quando o bind é `0.0.0.0`: linha amarela
  abaixo do status informando que o painel está em HTTP puro e que
  exposição na internet exige TLS na frente.
- Manual destacou que a senha padrão `admin/admin` deve ser trocada
  via `localhost` **antes** de liberar a porta no firewall — enquanto
  a senha for o default, qualquer host da LAN pode logar.

### Notes
- `Coletas` já tinha **Baixar JSON** e **Copiar** no header da v2.0.0;
  esta release apenas equipara o `Webhook logs` ao mesmo padrão.
- HTTP puro continua sendo o transporte do painel — exposição em IP
  público sem TLS expõe credenciais. Para LAN restrita está OK; para
  internet, ponha um TLS na frente (Caddy/Cloudflare Tunnel/nginx).

## [2.1.3] — 2026-05-12

### Added
- **Botão "Coletar agora"** na tela de Coletas. Dispara uma execução
  imediata de `collect_extensions` sem esperar o ciclo do scheduler.
  Rate-limit de 30 s por usuário admin pra evitar martelar o USCall.
  Endpoint: `POST /api/collections/run`.
- **Botões "Enviar agora"** (extensions / devices / results) na tela de
  Webhook logs. Diferente do "Testar" (que manda payload sintético),
  o "Enviar agora" pega o último snapshot real e dispara o webhook
  configurado. Para `devices` reexecuta o job de monitoramento (que
  dispara o webhook ao fim). Rate-limit de 15 s. Endpoint:
  `POST /api/webhooks/send/{event_type}`.

### Fixed
- **Auto-update via painel web não funcionava**. O endpoint
  `/api/system/update` chamava o instalador legado (tarball + NSSM/
  systemctl), que assume uma instalação tradicional com serviço e
  venv — nada disso existe no `.exe` standalone. Agora, quando o app
  detecta que está rodando empacotado (`sys.frozen`), o endpoint usa
  o mesmo fluxo do banner amarelo: baixa o `.exe` novo, lança um
  `.bat` helper detached que aguarda o PID atual morrer, troca o
  binário e re-abre o app. Em seguida sinaliza shutdown imediato da
  janela Tk + uvicorn, permitindo que o swap aconteça.
- Lógica de swap de `.exe` foi extraída de `desktop.py` para o módulo
  compartilhado `middleware_monitor.updater.standalone` para evitar
  duplicação entre o botão da janela Tk e o botão do painel web.

### Changed
- Tela de Webhook logs reorganizou os botões superiores em dois grupos
  visuais ("Enviar agora" em azul, "Teste" em cinza) pra deixar claro
  o que cada um dispara — antes só havia "Testar" e ele era confundido
  com envio real.

## [2.1.2] — 2026-05-12

### Added
- **Intervalo único e configurável de envio de webhooks** (em minutos,
  default 60, mínimo 1, máximo 1440). Substitui os três antigos
  `extensions_interval_seconds` / `devices_interval_seconds` /
  `results_interval_seconds` por um único `webhook_interval_minutes` no
  formulário de configuração. A cada ciclo a aplicação coleta os ramais
  no USCall, faz o ping dos dispositivos e dispara todos os webhooks
  habilitados. Reescalona o scheduler imediatamente ao salvar.
- `web/static/vendor/tailwindcss.js` (≈400 KB) — Tailwind agora é
  embarcado **dentro do `.exe`** e servido como asset estático, em vez
  de carregar do CDN. A UI passa a funcionar em ambientes sem acesso à
  internet (ex.: servidores corporativos restritos).

### Fixed
- **Timestamps em UTC sendo exibidos como se fossem locais** (causando
  diferença de 3 h em Brasília). A API agora serializa todos os
  timestamps com sufixo `Z` (`"2026-05-12T16:41:02Z"`) e o frontend usa
  `new Date(...).toLocaleString('pt-BR', { hour12: false })` para
  apresentar no fuso do navegador. Aplicado em Logs, Webhook events,
  Snapshots de coleta, Dispositivos, Detalhe do dispositivo, Dashboard
  e histórico de atualizações.
- **UI quebrava sem internet** porque o `base.html` carregava Tailwind
  de `https://cdn.tailwindcss.com`. Agora aponta para
  `/static/vendor/tailwindcss.js` (asset local), eliminando dependência
  externa em tempo de execução.

### Migration notes
- Instalações que já tinham `extensions_interval_seconds` /
  `devices_interval_seconds` / `results_interval_seconds` gravados no
  DB são migradas no boot: o maior dos três (em segundos) é convertido
  para minutos e populado como `webhook_interval_minutes`. Se nada foi
  configurado, o default é 60 minutos.

## [2.1.1] — 2026-05-12

### Fixed
- **Janela do CMD piscando a cada ping/arp no Windows.** O `.exe` é
  construído com `console=False`, e cada chamada a `ping.exe` / `arp.exe`
  via `asyncio.create_subprocess_exec` herdava o fato do pai não ter
  console, abrindo uma nova janela momentânea — incômodo visual e
  potencial impacto de performance ao monitorar centenas de ramais.
  Agora todos os filhos recebem `creationflags=CREATE_NO_WINDOW`.
- O `.bat` auxiliar do auto-update também era lançado via `os.startfile`,
  exibindo uma janela do prompt enquanto o swap acontecia. Agora é
  spawnado com `CREATE_NO_WINDOW | DETACHED_PROCESS` e stdio em
  `DEVNULL`, totalmente invisível.
- O `updater/installer.py` (caminho legado para hosts antigos com
  serviço Windows) também passou a usar a flag silenciosa em todos os
  `subprocess.run` (`nssm`, `sc`, `mklink`, `pip`, `alembic`).

### Notes
- Nenhuma mudança visível no Linux — o `os.name` continua `posix` e o
  caminho permanece intacto.

## [2.1.0] — 2026-05-12

Pivô na entrega no Windows: **abandonamos o instalador Inno Setup +
serviço Windows** e adotamos um **único `.exe` standalone** com janela
nativa (Tkinter), log integrado e auto-update no estilo Discord/OBS.

### Added
- `src/middleware_monitor/desktop.py` — entrypoint desktop que sobe o
  servidor uvicorn em thread daemon, expõe janela com status em tempo
  real, tail do log com cores por nível, abre o painel web e gerencia
  auto-update.
- **Auto-update no Windows**: banner amarelo "Nova versão disponível"
  que aparece quando há release nova no GitHub; 1 clique baixa, troca
  o `.exe` e reabre.
- `packaging/windows/exe/MiddlewareMonitor.spec` — PyInstaller spec
  que gera `MiddlewareMonitor-X.Y.Z.exe` (~30 MB, sem dependências).
- `packaging/windows/exe/build_exe.ps1` — build local rápido.
- Workflow `release.yml` reescrito: job `build-windows-exe` substitui
  `build-windows-installer`, usa PyInstaller direto, sem Inno Setup ou
  NSSM.
- MANUAL reescrito explicando o novo fluxo: clique-duplo → janela
  com status + log + auto-update.

### Changed
- Dados no Windows agora ficam em `%LOCALAPPDATA%\MiddlewareMonitor\`
  (escrita sem Admin) em vez de `C:\ProgramData\MiddlewareMonitor\`.
- `APP_HOST` padrão no Windows passa a ser `127.0.0.1` (loopback);
  para expor na rede, edite o `desktop.py` (futuramente faremos UI
  para isso).
- `APP_SECRET_KEY` é gerada e armazenada em `secret.key` no diretório
  de dados, isolado do executável.

### Removed
- `packaging/windows/inno/` — script Inno Setup descartado.
- `packaging/windows/payload/` — scripts de pós-instalação para serviço
  Windows não são mais necessários.
- `packaging/windows/build_installer.ps1` — substituído por
  `packaging/windows/exe/build_exe.ps1`.

### Notes
- O Linux **continua igual**: instalação via `.run` self-extracting
  com systemd e auto-update por cron 00:00. Funciona bem e não havia
  motivo para mudar.
- Se você instalou a v2.0.2 anteriormente (que registrou um serviço
  Windows), desinstale pelo Painel de Controle antes de rodar a v2.1.0,
  caso contrário os dois processos podem disputar a porta 8080.

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
