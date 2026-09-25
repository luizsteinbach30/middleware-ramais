# O middleware como agente do NOC WorkConnect

**Data:** 2026-08-31 · **Versão de referência:** v2.11.0 · **Atualizado:** 2026-09-15 (Fases 0 e 1 feitas, v2.13.0)
**Documento irmão:** `C:\Projetos\noc-workconnect\docs\CONTRATO-DO-AGENTE.md`

Este documento descreve o que **este** repositório precisa ganhar para participar do
programa NOC WorkConnect. Ele é a fonte da verdade sobre o lado do agente — o
repositório do NOC **aponta** para cá e não duplica nada.

---

## O que muda

Até a v2.13.0 o middleware fazia uma coisa em direção à internet: **empurrava** webhooks
(`extensions`, `devices`, `results`) para um endereço configurado. O módulo saiu na
**v2.14.0** (o dono: "não será mais utilizado") e hoje a única saída é a telemetria para
o NOC. Não existe nenhum caminho pelo qual alguém de fora peça qualquer coisa a ele.

Ele passa a ser um **agente ativo**, no modelo do Zabbix: além de empurrar
telemetria, ele **busca trabalho** numa fila do NOC, executa e devolve o resultado.

**O que não muda, em nenhuma fase:**

- **Nada entra.** A interface web continua ouvindo só na LAN. Nenhum *port forward*,
  nenhuma porta nova, nenhuma VPN permanente. Toda conexão continua saindo daqui.
- **As credenciais dos aparelhos não saem.** A tarefa que chega do NOC nomeia o alvo;
  quem sabe a senha é este processo. O que impede a saída é a **lista de permissão campo
  a campo** da telemetria (`domain/noc/telemetria.py`): o bloco `perfis` enumera o que
  vai, e `senha_sip`, `user_auth` e o `config_padrao` inteiro não estão lá.
- **E agora também não ficam em claro em repouso** (v2.14.0). Até a v2.13.0 este
  documento afirmava que elas ficavam "cifradas (Fernet) no SQLite local" — **e não
  ficavam**: `extension_lines.senha_sip` e as quatro senhas do `config_padrao`
  (`web_password`, `nova_web_password`, `menu_password`, `keylock_password`) estavam em
  texto claro, e a senha SIP ainda era enviada ao navegador a cada abertura da tela do
  ambiente. Passaram para a mesma `SecretBox` do token do USCall, com o prefixo
  `enc:v1:` marcando o ciphertext; a cifra vive na fronteira do banco
  (`domain/extension_configurator/repository.py`) e nenhum vendor precisou mudar.
  Instalação sem `APP_SECRET_KEY` utilizável continua gravando em claro, de propósito —
  a atualização que protege não pode ser a que derruba —, e passa a **dizer isso** em
  `GET /api/system/version` (`segredos_em_claro`).
- **O middleware continua funcionando sozinho.** NOC fora do ar não pode parar
  coleta, ping, nem o Configurador de Ramais. O agente é uma capacidade a mais, não
  uma dependência.

---

## As dez pendências

Cada uma diz **qual módulo existente se estende**. Não há módulo novo onde já existe
um — o valor do middleware está justamente no que ele já sabe fazer.

### 1 — `client_code` é texto livre digitado

`src/middleware_monitor/domain/config/schemas.py:54` · **Fase 0** · ✅ **feito (v2.13.0)**

O campo é digitado na tela de configuração. Dois clientes podem digitar o mesmo, e
qualquer um pode digitar o do vizinho. Não identifica ninguém e não se revoga.

**Vira:** tela de enrolamento — o operador cola um **código de uso único** gerado no
NOC, e o middleware o troca por `agente_id` emitido pelo servidor mais um segredo,
guardado como os outros segredos já são. O `client_code` pode sobreviver como rótulo
humano; não como identidade.

**Como ficou (2026-09-15):** tela `/system/noc` (`web/templates/system_noc.html`) →
`api/noc.py` → `domain/noc/cliente.py`. O navegador não fala com o NOC: quem troca o
código pela credencial é o servidor do middleware (a CSP é `connect-src 'self'`). A
credencial `ag_<id>.<segredo>` fica em `app_config` com prefixo `noc.`, cifrada pela
`SecretBox` (`domain/noc/estado.py`) — nunca em `/etc` nem no `.env`. Heartbeat em
`jobs/noc_agent.py`, agendado só depois do enrolamento (sem enrolamento não existe
conexão nenhuma com o NOC). Três decisões que valem dizer:

- **Nenhuma chave `noc.*` viaja no pacote portável** (`LOCAL_ONLY_KEYS` em
  `domain/backup/settings.py`): levar a credencial para outra máquina faria duas
  máquinas se apresentarem como o mesmo agente. O *snapshot* do banco, que restaura
  tudo, ainda leva — restaurar um snapshot antigo depois de um reenrolamento dá
  `credencial_recusada`, e o caminho é enrolar de novo.
- **Revogado para o laço; credencial recusada continua tentando.** Revogar é ato
  deliberado. Recusa pode ser o NOC com banco restaurado — parar a frota inteira seria
  uma visita por site.
- **Log só na mudança de situação** — todo WARNING vira linha em `system_logs`.

O `client_code` não mudou: continua como rótulo.

### 2 — Não existe canal de entrada

**Novo `src/middleware_monitor/jobs/noc_agent.py`, ao lado dos jobs que já existem** ·
**Fase 2** · ✅ **feito (v2.13.0)**

Hoje só há push (`domain/webhooks/sender.py`). Falta o laço de long-poll:

```
GET /agente/v1/fila?aguardar=25   ->  executa  ->  POST .../resultado
```

Pontos que o job precisa acertar, e que são fáceis de errar:
- **backoff com jitter.** Sem jitter, uma queda do NOC devolve a frota inteira no
  mesmo milissegundo.
- **reconexão imediata** após cada ciclo, incluindo o 204 de "nada a fazer".
- **um só laço**, no scheduler que já existe — não uma segunda thread paralela.

**Como ficou (2026-09-15):** `run_noc_tarefas` em `jobs/noc_agent.py`. A rota final é
`GET /agente/v1/tarefas?espera=25` (o NOC corta no teto dele, abaixo do timeout do
proxy) e `POST /agente/v1/tarefas/{id}/resultado` com `Idempotency-Key` = a chave que
veio na tarefa. Três decisões:

- **Job de disparo único que se re-agenda**, e não job de intervalo: um long-poll de
  25 s mais uma escrita de minutos atropelaria o disparo seguinte (`max_instances=1`
  pula e loga). Terminou bem → o próximo sai em 1 s; NOC fora → *full jitter* até 5 min.
  O heartbeat serve de vigia: se o laço sumiu, ele o rearma.
- **Outbox antes da fila.** Resultado que o NOC não confirmou fica em `noc_tarefas`
  (`concluida_em` preenchido, `entregue_em` vazio) e sai **antes** do próximo
  long-poll. 404/422 do NOC (tarefa que não é mais deste agente, ou reoferecida com
  outra chave) descarta: reenviar não muda a resposta e travaria o outbox inteiro.
- **Revogado para o laço**, como o heartbeat.

### 3 — Não existe executor de tarefa remota nem registro de idempotência

`src/middleware_monitor/domain/extension_configurator/actions.py` · **Fase 2** ·
✅ **feito (v2.13.0)**

As ações **já existem e estão homologadas**; só a tela as dispara. Falta a camada que
recebe uma tarefa do NOC, valida contra o manifesto, chama a ação existente e devolve
o resultado.

**Idempotência é requisito, não melhoria:** cada tarefa tem UUID; o agente guarda o
que executou e **tarefa repetida devolve o resultado gravado sem reexecutar**.
`send_config` reinicia o aparelho — uma reentrega por retry de rede derrubaria o
telefone duas vezes.

**Como ficou (2026-09-15):** `domain/noc/executor.py` + tabela `noc_tarefas` (migration
`0012`). Nenhuma ação nova: `normalize` chama `run_action_on_line`, reaplicar chama
`run_apply` com `selected_ids=[linha]`. As regras que o código carrega:

- **Tarefa repetida devolve o gravado.** Leitura reoferecida com **outra** chave roda de
  novo (repetir leitura não custa nada); escrita, com qualquer chave, nunca.
- **`iniciada_em` é confirmado no banco antes de tocar o aparelho.** Serviço que cai no
  meio de uma escrita volta dizendo "interrompida, resultado desconhecido" — e não
  reexecuta. Leitura interrompida é esquecida: o NOC reoferece quando o lease vence.
- **Escrita sem prazo não começa** (menos de 60 s de lease pela frente) e **sem backup
  não começa** (`create_snapshot(label="noc")` antes; falhou, a escrita não sai).
- **Escrita mira uma linha.** Ramal em duas linhas de ambiente é recusado — escolher
  uma seria palpite.
- **O raio é o daqui.** O NOC chamar de LEITURA algo que aqui é escrita é recusa.
- **Leitura não muda estado.** `ping` a pedido do NOC não grava em `devices`: marcar
  online um aparelho offline engoliria a volta que o vigia de recuperação usa para
  reaplicar config. Pelo mesmo motivo `coletar_agora` roda só a coleta do USCall, não
  o ciclo de ping.
- **Quem pediu fica na trilha local**: `operador = noc:<e-mail>` em
  `device_action_events` e `extension_apply_runs`, e a tela Sistema → NOC mostra a
  última tarefa.

### 4 — O manifesto de capacidades não é publicado para fora

`src/middleware_monitor/integrations/extension_configurator/vendors/base.py:134` ·
**Fase 0** · ✅ **feito (v2.13.0)**

A matéria-prima já existe: `capabilities()` por adapter e
`GET /api/extension-configurator/environments/{id}/capabilities` por ambiente. Falta agregar e enviar ao NOC no
registro e em cada heartbeat: unidades cobertas, servidores USCall e se estão
alcançáveis, ações homologadas, modelos presentes, versão e desvio de relógio.

**O NOC não infere nada** a partir de versão ou modelo. O que não estiver no
manifesto é recusado com `NAO_SUPORTADO` — nunca tentado em melhor esforço.

**Como ficou (2026-09-15):** `domain/noc/manifesto.py`. O heartbeat leva só o sha256
do manifesto; o NOC pede o corpo quando o hash não bate (nada volátil entra no corpo,
senão ele subiria a cada minuto).

- **`acoes` é a lista de permissão do executor** (`executor.ACOES`), desde a Fase 2.
  Na Fase 0 ia vazio, de propósito: declarar `normalize` antes do executor existir
  seria prometer ao NOC uma ação que ninguém aqui executaria. As capacidades locais
  vão por modelo, em `acoesDoAdapter` — `set_ip` aparece ali e nunca em `acoes`.
- **`ultimoBackupEm`** (Fase 3): o snapshot mais novo. Muda uma vez por backup, e é o
  que a tela de aprovação do NOC mostra antes de alguém autorizar uma escrita.
- **Modelo é o do ambiente** (`ExtensionEnvironment.modelo_telefone`), com a quantidade
  de linhas — o cadastro é a fonte da verdade, não o que o aparelho respondeu.
- **`uscall[].alcancavel` vem da última coleta** (`domain/uscall/saude.py`, em memória):
  `null` depois de um reinício, até a primeira coleta. O teste de conexão da tela baixa
  a lista inteira de ramais e é pesado demais para rodar a cada heartbeat.
- **Unidades não vão no manifesto**: quem diz quais unidades um agente cobre é o NOC,
  na hora de gerar o código.

### 5 — Webhook sem assinatura e sem chave de idempotência

**Fase 1** · ✅ **resolvido por outro caminho (v2.13.0) e encerrado na v2.14.0**

**Como ficou (2026-09-15):** o dono decidiu que **tudo o que os webhooks mandam vai para
o NOC**. A pendência não foi resolvida *no* `sender.py`: a telemetria para o NOC é canal
próprio (`domain/noc/telemetria.py` + `jobs/noc_agent.py::run_noc_telemetria`), com
**`Idempotency-Key` por lote** e **mTLS** no lugar do HMAC (ADR 0006 do NOC).

**Encerrada em 2026-09-16 (v2.14.0):** o módulo de webhooks foi removido — `sender.py`,
`api/webhooks.py`, a tela de logs, a tabela `webhook_events` e as chaves de destino.
O plano era desligá-lo na release **depois** do NOC em produção, para os receptores
atuais não ficarem sem dado; o dono antecipou, dizendo que não há mais receptor.
`webhook_interval_minutes` **não** foi apagada: ela sempre governou a cadência da coleta,
e virou `coleta_interval_minutes` (migration 0014).

**Isto já morde hoje, sem NOC nenhum:** com `RETRY_DELAYS_S = (0, 5, 30)`
(`sender.py:33`), um 200 perdido no caminho de volta faz o middleware reenviar — e
nada no protocolo permite ao receptor perceber que é o mesmo evento.

### 6 — Sem gzip

`src/middleware_monitor/domain/webhooks/sender.py` · **Fase 1** · ✅ **feito no canal do NOC (v2.13.0)**

O lote de telemetria vai em `Content-Encoding: gzip`. O webhook externo não ganhou gzip
de propósito: seria mudança de contrato para um receptor que vai deixar de existir.

O `docs/WEBHOOK_ARQUITETURA.md` deste repositório já marca gzip como o item de maior
retorno: cinco linhas de cada lado, −80 % de tráfego. Está escrito e nunca foi feito.

### 7 — Só três tipos de evento

`src/middleware_monitor/domain/config/repository.py:32` · **Fase 1** · ✅ **feito no canal do NOC (v2.13.0)**

**Como ficou:** o lote leva as transições de telefonia do MQTT local
(`extension_status_events`, por cursor), e o NOC deriva sozinho a queda e a volta de
rede e a mudança de registro comparando retratos consecutivos. A saúde do agente vai no
heartbeat (versão, relógio) e no manifesto (USCall alcançável). Além do que os webhooks
levavam: amostras de ping, relatórios de aplicação e o perfil de cada linha dos
ambientes — **nunca** `senha_sip`, `user_auth` ou chave com nome de segredo.

O que se pedia originalmente:

`WEBHOOK_TYPES` tem `extensions`, `devices`, `results`. Faltam dois:

- **mudança de estado** — ramal caiu, aparelho ficou offline. Hoje o NOC teria de
  inferir isso comparando snapshots, o que atrasa a detecção em um ciclo inteiro.
- **saúde do agente** — heartbeat com versão, desvio de relógio e alcance dos
  servidores USCall.

### 8 — Cliente MQTT é só consumidor

`src/middleware_monitor/integrations/mqtt_client.py` · **Fase 6**

O cliente só faz `subscribe`. Se o MQTT virar transporte alternativo do canal (ADR
0001 do NOC), falta o `publish`.

**A parte difícil já está pronta e homologada:** pinning de certificado,
`clean_session=False` com `client_id` estável e QoS 1 — que é o que faz o broker
guardar mensagem enquanto o serviço está parado. **Estende-se este cliente**, não se
escreve outro.

### 9 — O backup automático diário nunca roda de fato

`src/middleware_monitor/jobs/backup.py` · **Fase 3, e é pré-requisito** · ✅ **feito (v2.13.0)**

Ele só dispara com o app aberto às 02:30. Numa instalação desktop que fica fechada, o
backup simplesmente não acontece.

Isso deixa de ser inconveniência e vira bloqueio: **escrita remota exige backup
recente**. Correção mínima: rodar o backup atrasado no boot.

**Como ficou (2026-09-15):** `agendar_backup_atrasado` no boot — snapshot mais novo com
mais de 26 h (ou nenhum) agenda um backup para 3 min depois. Olha os **arquivos**, não o
`last_run_at`: backup manual conta, registro "ok" com a pasta apagada não conta. E toda
escrita remota faz o próprio snapshot antes, então nenhuma depende só deste.

### 10 — `ACTION_SET_IP` precisa sair do caminho remoto

`src/middleware_monitor/integrations/extension_configurator/vendors/base.py:37` ·
**Fase 3** · ✅ **feito (v2.13.0)**

`DEVICE_ACTIONS` (`base.py:36-38`) contém `set_ip`. Para uso local, com alguém na
frente do aparelho, tudo bem. **Pelo canal remoto, não.**

Errar a rede de um telefone a 800 km de distância é perder o aparelho até alguém ir
lá. A regra vale para qualquer P-code ou campo equivalente de IP, máscara, gateway,
DNS, VLAN, porta HTTP ou VPN — em todos os adapters.

**Como isso vira código:**
1. o executor remoto tem uma **lista de ações permitidas** que não inclui `set_ip`;
2. a whitelist de campos por fabricante já existente é reafirmada no caminho remoto;
3. **um teste que falha** se um código de rede entrar no caminho remoto. Sem o teste,
   a regra é um comentário.

**Como ficou (2026-09-15):** `executor.ACOES` não tem `set_ip` nem `send_config`, e
`tests/api/test_noc_tarefas.py` quebra se a lista mudar (acrescentar ação remota é
decisão, não detalhe), se alguma ação de aparelho além de `normalize` entrar nela, ou
se um campo com nome de rede (`ip`, `gateway`, `dns`, `vlan_*`, `P1234`…) passar pelo
`conferir`. Reaplicar usa o `generate_config` dos adapters, cuja whitelist de campos já
é testada por fabricante. O NOC recusa os mesmos campos do lado dele
(`P_CODE_DE_REDE`) — são duas barreiras que não dependem uma da outra.

---

## As pendências das telas v2 do NOC (2026-09-16)

O NOC redesenhou as telas com **o cliente como casa** (`noc-workconnect/docs/ADRs/0010`):

- os agentes moram dentro do cliente;
- a loja de cada agente **vem dos ambientes dele**, e não é mais escolhida;
- existe um **espelho do configurador de ramais** dentro de cada cliente;
- o Painel mostra se o coletor MQTT de cada agente está ouvindo.

Nada disso funciona com o que o middleware manda hoje. As três pendências abaixo são o que
falta **deste** lado. A especificação do outro lado está em `noc-workconnect/docs/TELAS.md`
v2, §0.6 e §12 a §17.

### 11 — O ambiente não viaja como entidade, e o nome não serve de identidade

`src/middleware_monitor/domain/noc/telemetria.py` · **Etapa I3 do NOC**

Hoje o ambiente só chega ao NOC como o **nome** repetido em cada linha de `perfis[]` e em
`aplicacoes[]`. O `id` (o slug de `ExtensionEnvironment.id`) nunca sai. Duas consequências:

- renomear um ambiente aqui separaria, lá, o histórico e o vínculo com a loja;
- não há como o NOC listar os ambientes, contar ramais e vinculados, nem mostrar a
  config padrão.

**O que o lote de telemetria passa a levar:**

- `ambientes[]`, **retrato completo** (ambiente que sumiu do retrato foi apagado aqui):
  - `id`, `nome`, `modelo`;
  - `ramais`, `vinculados` (linhas com `device_id`);
  - `situacao` (`ok` · `pendentes` · `erros` · `vazio`, a mesma do cartão da tela
    Ambientes);
  - `ultimaAplicacao` (`id`, `inicio`, `ok`, `total`);
  - `configPadrao`, montado **por lista branca**, nunca por lista negra:
    - chaves que vão com valor: `register_expiration` · `sip_account` ·
      `timezone_mode` · `timezone` · `ntp_mode` · `ntp_server` ·
      `validar_conectividade` · `verificar_registro_sip` · `keylock_enable` ·
      `keylock_timeout` · `hotline_enable` · `hotline_number` · `hotline_time` ·
      `function_keys`;
    - chaves que vão **só como `{ "definida": true|false }`**: as quatro de
      `defaults.py::CHAVES_SECRETAS` (`web_password` · `nova_web_password` ·
      `menu_password` · `keylock_password`) **e** `web_user` · `nova_web_user`, que são
      metade da mesma credencial;
    - chave que não está em nenhuma das duas listas **não sai** — e o teste quebra
      se `defaults.py` ganhar chave sem decidir de que lado ela fica;
  - `secoes` — as seções que o catálogo do fabricante oferece (`avancadas` só
    Intelbras, `hotline` só TIP, `teclas` só onde há teclas programáveis), para o NOC
    não oferecer o que o aparelho não tem;
  - `linhas[]` — `posicao`, `ramal`, `nomeVisivel`, `numeroAbreviado`, `ip`,
    `deviceId`, `status`, `ultimoModelo`, `ultimoMac`, `ultimaAplicacao`, `ultimoErro`.
    **Sem `senha_sip`, `user_auth` e `servidor_sip`.**
- `perfis[]` e `aplicacoes[]` passam a levar `ambienteId` além do nome.

**Por que a lista branca:** a senha SIP e as senhas web **já são cifradas em repouso**
(v2.14.0, `41fcc6e`), mas o `config_padrao` volta decifrado para a tela local. Um retrato montado por
"tudo menos as senhas" mandaria ao NOC a próxima chave de segredo que alguém acrescentar.
Do lado do NOC, uma invariante nova em `/sistema` conta as chaves fora da lista que
chegarem — e a contagem tem de ser zero.

> **✅ Feito em 2026-09-17** (`domain/noc/retrato.py`, testes em `tests/api/test_noc_retrato.py`).
>
> **Uma mudança de forma, com motivo: `configPadrao` é uma lista, não um objeto.** Cada
> item é `{ chave, valor }` ou `{ chave, definida }`. O NOC tira de todo lote as chaves com
> nome de segredo, em qualquer profundidade (`dominio/telemetria/telemetria.ts`). Um
> `{"web_password": {"definida": true}}` sumiria na entrada, e a tela diria que não há
> senha. Com o nome da chave como valor, o filtro de lá continua valendo e a informação
> chega. Há teste que passa o retrato pelo filtro do NOC e exige que nada se perca.
>
> **A terceira lista existe:** `CONFIG_NAO_SAI` guarda `sip_server`, `sip_transport`,
> `web_language` e `lcd_language`. O endereço do PBX é rede para o aparelho. Os idiomas
> ficam fora até o TELAS §14 pedir.
>
> **Campos a mais**, que as telas v2 do NOC usam:
> - `contagemPorStatus`, com o status fino da planilha (`applied`, `registered`, `pending`,
>   `outdated`, `error`, `invalid`);
> - `hora { timezone, ntpServer, origemFuso, origemNtp }`, que é o "herdado" do TELAS §14;
> - `atualizadoEm`;
> - nas linhas, `dispositivo` (o ramal do aparelho vinculado), porque o NOC conhece o
>   aparelho pelo ramal e não pelo `deviceId` local.
>
> **Medido na homologação** (4 ambientes): o lote monta em 132 ms e ocupa 2,6 KB com gzip.
> Nenhum dos 18 valores sensíveis reais aparece no lote.

### 12 — O estado do coletor MQTT não sai daqui

`src/middleware_monitor/integrations/mqtt_client.py` · `core/models.py::MqttConnectionEvent`
· **Etapa I3 do NOC**

O lote leva as **transições de telefonia** que o coletor observa, mas não diz **se o
coletor estava ouvindo**. Sem isso, "nenhuma mensagem nesta hora" no NOC não distingue
"ninguém publicou" de "o coletor estava fora do ar" — a mesma lição do ledger MQTT deste
repositório, que grava o histórico de conexão junto justamente por isso.

**O que passa a sair**, no heartbeat (estado atual) e no lote (histórico):

- `coletor: [{ broker, endereco, estado, desde, detalhe, mensagens24h, ultimaMensagemEm }]`
  — `estado` em `conectado` · `desconectado` · `sem_broker`;
- `conexoesMqtt[]` — os `MqttConnectionEvent` novos desde o cursor, com `em`, `estado` e
  `detalhe`;
- `mensagensPorHora[]` das últimas 24 h, só das horas em que o coletor estava conectado
  (hora sem conexão **não vai como zero**).

> **✅ Feito em 2026-09-17** (`domain/noc/retrato.py`).
>
> **Uma mudança de lugar, com motivo: o estado atual (`coletor[]`) vai no lote, não no
> heartbeat.** O heartbeat do NOC é um DTO fechado (`forbidNonWhitelisted`), e um campo
> novo ali faria todo NOC que ainda não o conhece responder 400. O agente ficaria sem
> conexão. O lote é JSON lido chave a chave, já sai a cada minuto e continua com
> `versaoDoContrato: 1`.
>
> **As formas:**
> - **`coletor[]`:** `{ brokerId, broker, endereco, estado, desde, detalhe, mensagens24h,
>   ultimaMensagemEm }`.
>   - `endereco` é só `host:porta`: usuário e senha do broker nunca entram.
>   - Sem broker ligado, a lista traz **uma** entrada `estado: "sem_broker"`. Lista vazia
>     seria indistinguível de "este agente não informa o coletor".
>   - Com o coletor rodando, o estado vem da memória. Sem ele, vem do último evento gravado.
> - **`conexoesMqtt[]`:** `{ id, brokerId, em, estado, detalhe }`, por cursor, começando
>   24 h para trás.
>   - `estado` é o do ledger: `startup`, `connected`, `subscribed`, `disconnected`, `error`
>     ou `stopped`.
>   - `startup` e `stopped` têm `brokerId: null`, porque valem para todos os brokers.
> - **`mensagensPorHora[]`:** `{ brokerId, broker, hora, mensagens, coberturaPct }`, pela
>   mesma prova de cobertura da tela local (`domain/mqtt/coverage.py`).
>   - Hora com cobertura e sem mensagem vai com `0`: é silêncio medido.
>   - Hora sem nenhum segundo de cobertura não vai.

### 13 — Edição central: escrever na planilha e na config padrão a pedido do NOC

`src/middleware_monitor/domain/noc/executor.py` · **Etapa I5 do NOC** · ✅ **feito (17/09, main local)**

O NOC vai permitir editar, por pedido aprovado por outra pessoa, o que **não é segredo
nem rede**. Dois verbos novos no executor, ambos de raio `ESCRITA REVERSÍVEL`:

- **`editar_linha_do_ambiente`** — `{ ambienteId, ramal, campos: [{ campo, de, para }] }`.
  - Campos permitidos: `nomeVisivel` e `numeroAbreviado` (os nomes do retrato do item 11).
  - Um pedido por ramal, com os campos do ramal juntos.
- **`editar_config_do_ambiente`** — `{ ambienteId, campos: [{ chave, de, para }] }`.
  - Chaves permitidas: as que o item 11 manda **com valor** (`retrato.CONFIG_COM_VALOR`).

O contrato inteiro, com o resultado e os códigos de recusa, está em
`noc-workconnect/docs/CONTRATO-DO-AGENTE.md` §10.

**As garantias, cada uma com teste:**

1. **Lista branca no executor**, independente da do NOC. `ip`, `senha_sip`, `user_auth`,
   `servidor_sip` e qualquer chave de credencial são recusados — duas barreiras que não
   dependem uma da outra, como no item 10.
2. **Conferência do `de`.** Se o valor atual aqui for diferente do `de` que o NOC viu no
   retrato, a tarefa volta **`RECUSADA` com o valor atual**, e nada é gravado. O NOC nunca
   sobrescreve uma mudança feita na loja.
3. **Validação do fabricante** antes de gravar (o `;` do TIP 125i, por exemplo) — a mesma
   que a planilha local usa.
4. **Backup antes**, como toda escrita remota.
5. **Grava na planilha, não no aparelho.** A linha fica `desatualizado` (e, na config
   padrão, todas as linhas do ambiente), e a reaplicação continua sendo o verbo
   `reaplicar_config_do_ambiente`, ramal a ramal.
6. **Releitura depois de gravar**, e o resultado devolve o que ficou gravado. Um "ok" sem
   releitura não prova nada.

**Não entra:** criar, duplicar ou apagar ambiente pelo NOC. Ambiente novo precisa da
credencial dos aparelhos, que não sai daqui.

**Revisão de 17/09 (ADR 0012 do NOC — a planilha do NOC é a do middleware), que vale sobre o texto acima:**

- `editar_linha_do_ambiente` **saiu**. Entrou **`editar_planilha_do_ambiente`** `{ ambienteId, de, para }`: a planilha
  inteira que o NOC viu e a desejada, com as sete colunas (ramal, ip, userAuth, senhaSip, servidorSip,
  numeroAbreviado, nomeVisivel), linha nova (`origem: null`), remoção (posição não citada) e ordem nova; a linha que
  veio de outra mantém o `id` (vínculo com aparelho e histórico).
- A conferência é da **planilha inteira** (`DE_DIVERGENTE` com `atual`, senha como `definida`); validação por linha
  (IP, ramal obrigatório, sonda do fabricante); backup; `save_lines`; releitura. **Senha nunca em recusa nem em
  resultado.**
- O retrato leva `userAuth`, `senhaSip` e `servidorSip` **só** em `ambientes[].linhas[]` (o NOC lê por lista branca e
  cifra ao receber). Credenciais da config padrão continuam só `definida`.
- `reaplicar_config_do_ambiente` aceita `{ ambienteId, posicao }` e **não exige aparelho vinculado**
  (`LINHA_NAO_ENCONTRADA` para posição que não existe).

**Como ficou (17/09):**

- **Recusa com código.** `naoSuportado: true` e `resultado.recusa` ∈ `CAMPO_NAO_PERMITIDO`,
  `DE_DIVERGENTE` (com `atuais`), `VALOR_INVALIDO`, `AMBIENTE_NAO_ENCONTRADO`,
  `RAMAL_NAO_ENCONTRADO`, `RAMAL_AMBIGUO`. Campo de rede ganha "(é de rede)" na frase.
- **O `de` é conferido duas vezes:** antes do backup (recusa barata, sem snapshot à toa) e de
  novo dentro da transação que grava, porque a planilha pode mudar durante o backup.
- **Igualdade estrita** na config: `True` não é `1`, e o `para` precisa ter o tipo do valor
  guardado.
- **Validação do fabricante:** na linha, uma sonda com os valores novos passa pelo adapter do
  modelo (é assim que o `;` do TIP 125i é recusado); na config, `validate_config_padrao` com a
  config nova (hotline ligada sem número, no TIP).
- **O `status` devolvido é o relido**: `outdated` para linha que já tinha sido aplicada;
  `pending`/`registered` para a que nunca foi. Na config, `linhasDesatualizadas` conta as linhas
  que passaram a `outdated`.
- **Nenhum caminho até o aparelho:** o teste troca `run_apply` e `run_action_on_line` por funções
  que quebram se forem chamadas (`tests/api/test_noc_edicao.py`).
- Os dois verbos entram em `executor.ACOES` e, por isso, no manifesto.

### 14 — Túnel de acesso web: abrir a interface de um equipamento a partir do NOC

`src/middleware_monitor/domain/noc/tunel.py` · **ADR 0007** · ✅ **feito (25/09, branch `feat/acesso-web-remoto`)**

Uma pessoa no NOC abre, no navegador dela, a interface web de um equipamento da rede do cliente ou do painel de um
USCall cadastrado aqui. **A conexão sai daqui**: a tarefa `abrir_acesso_web` chega pelo long-poll, e o middleware
abre um WebSocket de saída em `agente/v1/tunel/{sessao}` (mTLS + Bearer). O contrato dos frames está em
`noc-workconnect/docs/CONTRATO-DO-AGENTE.md` §11.

- **Pedido:** `{ sessao, tipoDeDestino: "lan", destino, porta, esquema }` ou `{ sessao, tipoDeDestino: "uscall",
  uscall }`. Resultado: `{ aberto, reaberto, destino }`.
- **Destino conferido aqui:** só IPv4 privado em `lan`, e só pelo nome do cadastro em `uscall`.
- **Exceção declarada ao item 10:** pelo túnel a pessoa alcança a página de rede do aparelho. As tarefas continuam
  sem campo de rede.
- **Sem credencial injetada**, 60 minutos no máximo, reconexão limitada, e cada sessão no log com a pessoa
  (`noc_tunel_aberto` / `noc_tunel_encerrado`).

---

## Resumo por fase

| Fase | O que este repositório entrega |
|---|---|
| **0** ✅ | enrolamento (1) · manifesto publicado (4) · heartbeat — **v2.13.0** |
| **1** ✅ | telemetria por cursor com `Idempotency-Key` e gzip (5, 6, 7) · **mTLS adiantado da Fase 6** — **v2.13.0** |
| **2** ✅ | laço de long-poll (2) · executor com idempotência (3) — **v2.13.0** |
| **3** ✅ | backup diário que roda (9) · `set_ip` fora do remoto, com teste (10) — **v2.13.0** |
| **6** | `publish` no cliente MQTT (8) · ~~mTLS~~ (feito na Fase 1) |
| **I3 do NOC** ✅ | retrato de `ambientes[]` com `id` e lista branca (11) · estado do coletor MQTT (12) — 2026-09-17, no lote de telemetria |
| **I5 do NOC** | edição central com conferência do `de` e releitura (13) |
| **Túnel** | acesso web a equipamento da LAN e a USCall cadastrado, com a conexão saindo daqui (14) — 2026-09-25 |

---

## 🔗 Relacionado

- `WEBHOOK_ARQUITETURA.md` — a *arquitetura D* descrita ali (pull com outbox e
  cursor) é exatamente este canal, e as recomendações de gzip, `Idempotency-Key`,
  ack em 202 e `Retry-After` continuam valendo
- `TELAS.md` — as telas deste app; o enrolamento ficou em página própria, `/system/noc`
- `RUNBOOK.md` · `INSTALACAO.md`
- `C:\Projetos\noc-workconnect\docs\CONTRATO-DO-AGENTE.md` — o outro lado do canal
- `C:\Projetos\noc-workconnect\docs\CATALOGO-DE-TAREFAS.md` — o enum fechado e os raios
