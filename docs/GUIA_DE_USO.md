# Guia de Uso — Middleware USCall Monitor

Guia funcional completo: **o que o sistema faz** e **como configurar cada
função**. Para instalação/atualização do executável, veja
[`MANUAL.md`](MANUAL.md); para diagnóstico de incidentes, veja
[`RUNBOOK.md`](RUNBOOK.md).

---

## 1. Visão geral

O Middleware USCall Monitor é um painel local que faz três coisas:

1. **Monitora ramais SIP** — a cada ciclo coleta o status dos ramais no USCall
   (PBX), pinga os dispositivos na rede e mostra tudo num painel.
2. **Dispara webhooks** — envia o resultado das coletas (ramais, devices,
   resultados) para sistemas externos.
3. **Provisiona telefones em massa** (Configurador de Ramais) — aplica a
   configuração SIP/teclas nos próprios aparelhos via a interface web deles,
   agrupando-os em **ambientes**.

Cada ciclo (intervalo configurável) executa, em ordem: **coleta USCall →
ping dos devices → webhooks**. Há ainda jobs de retenção (limpeza) e de
verificação de atualização.

**Dois status independentes por ramal/device:**

| Status | Origem | Valores |
|---|---|---|
| **Lógico** (USCall) | PBX reporta se o ramal está registrado | `disponível` / `indisponível` |
| **Rede** (ICMP) | ping do middleware ao IP | `online` / `offline` |

Entender essa diferença é a chave para o resto do guia: um telefone pode estar
**online na rede** (responde ping) mas **indisponível no PBX** (não registrou) —
sinal de que a config sumiu/mudou.

---

## 2. Primeiro acesso

1. Abra o painel (`http://localhost:8080/` na máquina, ou
   `http://<ip-do-servidor>:8080/` na LAN).
2. Login inicial: **`admin` / `admin`** (troca obrigatória no primeiro acesso).
3. **Troque a senha do admin antes de liberar a porta no firewall.**

Sessão: cookie HttpOnly, expira em 12 h, renova por atividade. Bloqueio após 5
tentativas de login falhas em 10 min.

---

## 3. Telas do painel

### 3.1 Dashboard (`/`)
Cartões com total de devices, online/offline (rede), disponível/indisponível
(lógico), latência média/máxima, última coleta, webhooks por status nas últimas
24 h, versão e status do updater.

### 3.2 Devices (`/devices`)
Lista paginada de todos os ramais coletados, com o vínculo a ambiente (quando
houver), status lógico e de rede, latência e "última vez visto".

**Filtros disponíveis:**

| Filtro | Como usar |
|---|---|
| Buscar | texto livre por ramal ou IP |
| **Ambiente** | campo com **busca** — digite o nome e a lista filtra; inclui "Todos" e "Sem vínculo". Feito para muitos ambientes |
| Status de rede | online / offline / desconhecido |
| Status lógico | disponível / indisponível |
| **IP de / até** | faixa de IP (comparação numérica) |
| **Ramal de / até** | faixa numérica de ramal |

**Ações em massa** (marque os checkboxes nas linhas; a barra azul aparece):

- **Apagar** — remove os devices selecionados. As **linhas de ambiente
  vinculadas são preservadas** (apenas desvinculadas); o histórico de pings é
  removido.
- **Adicionar a ambiente** — cria uma linha por device num ambiente existente.
- **Criar ambiente** — pede nome + modelo e já adiciona os devices como linhas.

Em ambos os "adicionar/criar", só os **campos conhecidos** do device são
preenchidos na planilha (IP e número do ramal); o resto herda os defaults do
ambiente. Devices **já vinculados** ou com **IP já presente** no ambiente são
**pulados** (o toast informa quantos entraram e quantos foram pulados).

- **Forçar ping** (cabeçalho) — dispara um ciclo de monitoramento imediato.
- **Exportar CSV** — exporta a lista de devices.

### 3.3 Coletas (`/collections`)
Histórico dos snapshots coletados do USCall, paginado e filtrável por data/tipo.
Clique para ver o payload completo.

### 3.4 Webhook logs (`/webhook-logs`)
Cada envio de webhook (com retry) vira um evento: tipo, URL, status HTTP,
duração, payload e resposta. Permite **reenviar**.

### 3.5 Logs (`/logs`)
Eventos `WARN`/`ERROR` persistidos, filtráveis por nível e módulo.

### 3.6 Atualizações (`/system/updates`)
Histórico de auto-updates, troca de canal (`stable`/`beta`), pausar updates
automáticos e "Verificar agora". O app consulta o GitHub Releases e, quando há
versão nova no canal, baixa, valida hash, roda as migrations e reinicia.

---

## 4. Configuração geral (`/config`)

Cada bloco da tela e o que cada campo faz:

### 4.1 Identificação do cliente
- **`client_code`** — slug que identifica o cliente no payload dos webhooks.

### 4.2 Identidade visual *(novo na v2.5.0)*
- **Logo** (png/jpg/svg/webp/gif, máx 2 MB) — aparece na **sidebar**, na **tela
  de login** e no **cabeçalho dos relatórios PDF**.
- **Favicon** (ico/png/svg) — ícone na **aba do navegador**.
- Envie pelo botão "Enviar arquivo"; a prévia aparece ao lado, com botão
  **Remover**. Aplica na hora (recarregue a página para a sidebar atualizar).

### 4.3 Integração USCall
- **`uscall_host`** — host do PBX **sem `https://`**.
- **`uscall_token`** — token de acesso (sensível; mascarado após salvo, clique
  "Alterar" para trocar).
- **`verify_ssl`** — validação do certificado TLS (deixe ligado; desligue só em
  emergência com certificado quebrado).
- **Testar conexão** — valida host+token e mostra HTTP/latência.

### 4.4 Intervalo de envio dos webhooks
- **`webhook_interval_minutes`** — período do ciclo coleta→ping→webhooks
  (1 a 1440 min).

### 4.5 Monitoramento de rede
- **`ping_timeout_ms`** — timeout de cada ping.
- **`ping_concurrency`** — pings simultâneos (máx 200; reduza em redes frágeis).
- **`device_ping_retention_days`** — por quantos dias guardar o histórico de
  pings (maior consumidor de disco).

### 4.6 Auto-reaplicação de configs
Quando um telefone vinculado a um ambiente está acessível mas **não registrado**
no PBX, o sistema pode **reaplicar a config sozinho** para reprovisioná-lo.

- **`auto_reapply_on_recovery`** — liga/desliga o comportamento (vem
  **desligado**). **Sem isso, nenhuma reaplicação automática acontece.**
- **`auto_reapply_debounce_minutes`** — intervalo mínimo entre tentativas por
  linha (evita storms em redes instáveis).

Regras (ver detalhes em [§6](#6-auto-reaplicação-watcher-de-recovery)):
- Dispara quando o device responde ICMP **e** está `indisponível` no PBX —
  inclusive quando ele **nunca caiu da rede** (config alterada sem reiniciar).
- Após falhar com **as duas credenciais**, **para de tentar** e mostra o erro na
  tela do ambiente, até o telefone reregistrar ou você reaplicar manualmente.

Há também o botão **"Vincular por IP agora"** — casa ExtensionLines órfãs com
devices de mesmo IP (idempotente).

### 4.7 Webhooks
Três tipos: **extensions** (ramais), **devices** (rede) e **results**. Para cada
um: ligar/desligar, **URL** de destino, **token** (`Authorization: Bearer`),
e botão **Testar** (envia payload `test=true`). Em falha de rede há retry com
backoff (3 tentativas).

### 4.8 Retenção e limpeza
- **`webhook_log_retention_days`**, **`collection_retention_days`**,
  **`system_log_retention_days`** — janelas de retenção de cada tabela. Um job
  diário remove o que passou do prazo.

---

## 5. Configurador de Ramais

Provisiona telefones SIP em massa pela interface web dos próprios aparelhos.
Sem fingerprint/discover automático — **o modelo cadastrado no ambiente é a
fonte da verdade** — e **nunca** mexe em configuração de rede do aparelho.

Modelos suportados: HTEK (UC9xx), Intelbras (V-series e S3002), FlyingVoice P10 e
Yealink T31G.

### 5.1 Ambientes (`/extension-configurator/environments`)
Cada **ambiente** agrupa telefones do mesmo modelo com uma **configuração
padrão** compartilhada. A lista mostra cards com nome, modelo, nº de ramais,
quantos devices estão vinculados e o status agregado.

- **Filtrar/buscar** por nome, modelo ou status.
- **Novo ambiente** — nome + modelo.
- **Exportar** *(novo na v2.5.0)* — marque os checkboxes dos cards e use
  **XLSX** ou **PDF** na barra de filtros. Sem seleção, exporta os ambientes
  visíveis. O relatório traz modelo, configurações e a tabela de ramais
  (senhas mascaradas; logo no topo do PDF).

### 5.2 Configuração padrão do ambiente (`…/config`)
Campos mesclados com os defaults na leitura. Principais:

| Campo | O que é |
|---|---|
| `sip_server` | servidor SIP (proxy/registrar) |
| `sip_transport` | udp / tcp / tls |
| `register_expiration` | expiração do registro (s) |
| `ntp_server`, `timezone` | hora do aparelho |
| `web_language`, `lcd_language` | idiomas |
| `web_user` / `web_password` | **credencial atual** do aparelho (default `admin`/`admin`) — usada para autenticar no envio |
| `nova_web_user` / `nova_web_password` | **nova credencial** a gravar no aparelho; também usada como **fallback** de login |
| `menu_password`, `keylock_*` | bloqueio de menu/teclado (Intelbras) |
| `function_keys` | teclas programáveis (line / speed_dial / blf / disabled) |

### 5.3 Planilha de linhas (ramais)
Por linha: **IP**, número do ramal, user auth, senha SIP, servidor SIP
(herda do ambiente se vazio), número abreviado, nome visível. Salvar substitui
a planilha; linhas com IP igual a um device existente são **vinculadas
automaticamente**.

### 5.4 Aplicar a configuração
Pipeline minimalista: **ping (opcional) → envia a config**. O aparelho
normalmente reinicia ao aceitar. Há *rolling delay* entre disparos para não
sobrecarregar a rede. Pode aplicar tudo, só os pendentes/erros, ou uma seleção.

**Credenciais e fallback** *(v2.5.0)*: o envio tenta primeiro a credencial
atual (`web_user`/`web_password`). Se o aparelho **recusar** (senha já trocada
numa aplicação anterior), tenta automaticamente a **nova credencial**
(`nova_web_user`/`nova_web_password`). Só falha de fato se as duas forem
recusadas.

Estados por linha: `pending` → nunca aplicado · `applied` → aplicado e em dia ·
`outdated` → config mudou após aplicar · `error` → última aplicação falhou.

### 5.5 Relatórios de execução (`/extension-configurator/runs`) *(v2.5.0)*
Cada execução vira um relatório com cartões (total/OK/falha/duração/**operador**)
e a tabela dos **ramais impactados** mostrando **status antes → depois** e o
erro de cada. É um **snapshot do momento** — reflete o que aquela execução fez,
não o estado atual do ambiente. Execuções anteriores à v2.5.0 mostram o estado
atual, marcadas como "execução antiga".

---

## 6. Auto-reaplicação (watcher de recovery)

Com `auto_reapply_on_recovery` ligado, a cada ciclo de monitoramento o sistema
procura telefones que precisam ser reprovisionados e reaplica a config.

**Quando reaplica** — device é candidato quando:
- voltou de `offline → online` no ICMP, **ou**
- responde ICMP (`online`) mas o PBX o reporta `indisponível` — **mesmo que
  nunca tenha caído da rede** (caso típico: a config foi alterada/perdida e o
  registro SIP caiu, sem o telefone reiniciar).

A reaplicação só ocorre se o PBX vê o ramal como `indisponível`; se está
`disponível`, está tudo certo e nada é feito.

**Quando desiste** — após uma reaplicação que falhou (tendo tentado a
credencial atual **e** a nova), o sistema **para de insistir** naquele ramal e
deixa o erro visível no ambiente. Volta a tentar apenas quando:
- o telefone **reregistra** (fica `disponível` de novo, abrindo um novo
  episódio), **ou**
- você **reaplica manualmente**.

O `auto_reapply_debounce_minutes` limita a frequência das tentativas.

---

## 7. Vínculo Device ↔ Linha

- O vínculo é feito por **IP**: quando o USCall traz um device cujo IP bate com
  uma linha, eles são vinculados automaticamente. O botão "Vincular por IP
  agora" (em `/config`) força isso a qualquer momento.
- **Se o IP do ramal muda na coleta**, o novo IP é propagado para o device e
  para a linha vinculada (a planilha do ambiente reflete sozinha).
- A tela de detalhe do device permite vincular/desvincular manualmente e
  aplicar a config só naquele ramal.

---

## 8. Segurança e dados

- Tokens (USCall, webhooks) são **criptografados em repouso** com chave derivada
  de `APP_SECRET_KEY`. Logs nunca contêm os tokens.
- Senhas de usuários com bcrypt; CSRF nas mutações; cookies HttpOnly/SameSite.
- **O Configurador nunca emite configuração de rede** dos aparelhos (IP, máscara,
  gateway, DNS, VLAN, VPN, portas, Wi-Fi) — garantido por whitelist + testes.
- **Onde ficam os dados (Windows):** `%LOCALAPPDATA%\MiddlewareMonitor\`
  (`db\` SQLite, `backups\`, `logs\`, `tmp\`, `branding\`, `secret.key`).
  Apagar essa pasta zera o sistema (recria schema, admin/admin e nova chave).

---

## 9. Atualização do sistema

O updater compara a versão em execução com a última release do canal no GitHub.
Ao atualizar, baixa o pacote, valida o `SHA256SUMS`, roda `alembic upgrade head`
(aplica migrations novas, como a `0004` desta versão) e reinicia. Em falha de
migração ou de inicialização, faz **rollback automático** para a versão anterior.

---

## 10. Backup e restauração (`/system/backup`)

São **duas coisas diferentes** na mesma tela, e escolher a errada custa caro na
hora do aperto:

| | Pacote de configuração (`.mwrbak`) | Backup do banco (`.db.gz`) |
|---|---|---|
| Serve para | levar a configuração para **outra** instalação | recuperar **esta** instalação |
| Leva histórico? | não (só configuração) | sim: coletas, pings, ledger MQTT, chamadas |
| Tamanho | KB a poucos MB | proporcional ao banco (58 MB → ~4 MB comprimido) |
| Como restaura | aplica na hora, seção a seção | troca o banco no próximo boot |

### Levar a configuração para outro sistema

1. Em **Exportar configuração**, marque o que levar (por padrão vai tudo:
   configurações do sistema, ambientes, usuários e devices).
2. Escolha uma passphrase e clique em *Exportar arquivo*. **Guarde a
   passphrase**: sem ela o arquivo não abre, nem por aqui nem por ninguém.
   O arquivo carrega o token do USCall, a senha do broker e a senha SIP de cada
   ramal — é por isso que é cifrado.
3. No outro sistema, em **Importar configuração**, selecione o arquivo, informe
   a passphrase e clique em *Analisar arquivo*.
4. **Resolva os conflitos.** A tela compara o arquivo com o que já existe e
   separa em três:
   - *igual* — some do caminho, vira só uma contagem, e não é gravado de novo;
   - *novo* — entra;
   - *em conflito* — abre com uma tabela mostrando, campo a campo, o que está
     **no sistema** e o que está **no arquivo**; você escolhe *Manter atual* ou
     *Usar do arquivo*. Se forem muitos, dá para decidir o grupo inteiro de uma
     vez pelos botões *todos: manter* / *todos: do arquivo*.

   O padrão é o arquivo, exceto em **Usuários**, onde é o que já está no
   sistema — restaurar não troca a senha de quem está operando sem você mandar.
   Valor de senha ou token não aparece na comparação: a tela só diz que difere.
5. Escolha as seções e o modo:
   - **Mesclar** — nada é apagado. O que existe só no sistema continua lá.
   - **Substituir** — além disso, **apaga** os ambientes, servidores USCall e
     brokers MQTT que existem só no sistema (a confirmação diz quantos são).
     Ambiente apagado leva junto o histórico de aplicação dele.
6. Usuários e devices nunca são apagados na importação, em modo nenhum: os que
   faltam são criados, e os existentes só mudam se você decidir pelo arquivo.

### Recuperar a instalação

- *Gerar backup agora* cria uma cópia completa do banco na pasta de backups.
- **Backup automático do banco** roda todo dia no horário configurado (relógio
  do servidor). A poda respeita a quantidade de cópias **e** o teto de espaço —
  vale o corte que vier primeiro, e o último backup nunca é apagado.
- Se você salvar uma passphrase em *Passphrase do pacote automático*, cada
  execução grava também o `.mwrbak` — assim o backup diário já sai com a parte
  portável pronta.
- Para restaurar, clique em *restaurar* na linha do arquivo (ou envie um
  `.db.gz` do seu computador pelo seletor no alto da tabela). O arquivo é
  conferido na hora; a **troca acontece na próxima inicialização** do
  middleware, e até lá o sistema continua rodando normalmente. Enquanto estiver
  agendada, um aviso amarelo fica no topo da tela, com o botão de cancelar.
- Depois de reiniciar: tudo o que foi gravado **depois** daquele backup se
  perde, e o banco substituído fica guardado na pasta de backups como
  `pre-restore-<data>.db` — dá para voltar atrás copiando-o por cima.

### Perguntas que aparecem

- **Perdi a passphrase do `.mwrbak`.** Não há recuperação. Gere um novo export.
- **Importei e não mudou nada.** Provavelmente estava tudo igual — a tela mostra
  quantos itens foram ignorados por já baterem com o arquivo. Reimportar o mesmo
  pacote duas vezes não faz nada na segunda vez.
- **Restaurei mas o conflito continua na lista.** Se você escolheu *Manter
  atual*, o sistema segue diferente do arquivo de propósito — é isso que a
  linha está dizendo.
- **O backup é de uma versão mais nova do middleware.** A restauração é
  recusada: o banco traria um schema que a versão instalada não sabe ler.
  Atualize o middleware primeiro.
- **Quanto espaço isso ocupa?** O rodapé da tabela mostra o total. Se apertar,
  baixe o limite em MB — a poda seguinte já obedece.

---

## 11. Coletor MQTT

### Para que serve

O PBX publica num broker MQTT (EMQX) o estado de cada ramal — disponível,
tocando, discando, em conversa, indisponível. O middleware assina esse broker e
faz três coisas com o que chega:

| Tela | Pergunta que responde |
|---|---|
| **Painel ao vivo** (`/mqtt-painel`) | o que está acontecendo agora |
| **Chamadas** (`/mqtt-chamadas`) | quem falou com quem, quanto tempo, como terminou |
| **Mensagens** (`/mqtt-messages`) | essa mensagem foi mesmo publicada? |

A terceira é a razão original da integração: **o serviço que publica não
registra os próprios envios**. Quando aparece a dúvida "o sistema mandou esse
evento?", é aqui que existe a prova.

### Configurar (uma vez)

Em **Configuração** (`/config`), seção *Coletor de mensagens MQTT*:

1. Digite o endereço do broker no campo único — pode ser `emqx.exemplo.com`,
   `emqx.exemplo.com:8883` ou até a URL do painel web do EMQX. Clique em
   **Descobrir conexão**.
2. O sistema **sonda** a rede e mostra o relatório passo a passo: qual porta
   respondeu, se tem TLS, se o broker exige credencial. Ele não confia em porta
   aberta — só aceita endpoint que responde CONNACK de MQTT.
3. Se o certificado não for de uma CA conhecida, a tela mostra a impressão
   digital e pede sua confirmação. Confirmada, aquele certificado — e só ele —
   passa a ser aceito.
4. Informe usuário e senha se o broker exigir, e escolha os **tópicos** a partir
   do que existe no broker (a sonda lista o que está passando).
5. Defina a **retenção**: dias de ledger e, opcionalmente, um teto em MB.

Depois disso o coletor conecta sozinho a cada inicialização e reconecta se cair.

### Painel ao vivo

Um cartão por ramal, colorido pelo estado, com a outra ponta da chamada e há
quanto tempo está assim. Clicar num contador (Tocando, Em conversa…) filtra a
grade. O número do ramal leva às mensagens cruas dele.

- **Faixa âmbar no topo**: o coletor não está rodando ou não há broker — a grade
  abaixo é o **último estado conhecido**, não o de agora. Preste atenção nisso
  antes de concluir qualquer coisa.
- **Ramal apagado com "sem msg há X"**: o publicador parou de falar daquele
  ramal há mais de 2 minutos.
- **"sem device"**: o ramal existe no PBX mas ainda não como telefone
  monitorado. Normal — quem cria o device é a coleta REST, porque o payload MQTT
  não traz IP nem MAC.
- **Descartadas > 0** (em vermelho, na saúde da ingestão): mensagem que não foi
  gravada é prova perdida. Se aparecer, avise.

### Chamadas

Cada linha é **uma ponta**: ligação entre dois ramais aparece duas vezes, uma
para cada lado; um grupo de captura aparece uma vez por ramal que tocou. Filtre
por período, ramal, outra ponta, direção e resultado, e exporte em CSV (abre no
Excel em português, com hora local).

O **resumo diário por ramal** (usado na seção Telefonia do device) conta
diferente de propósito: uma chamada com identificador conta **uma vez por
ramal**, mesmo tendo tocado em vários. Sem isso, um grupo de captura inflaria as
perdidas em quase três vezes — foi medido.

### Mensagens (a prova)

Busque por período, tópico (aceita `+` e `#`), ramal ou texto do conteúdo. Cada
mensagem abre com o payload **como recebido** (é ele que vale como prova) e uma
aba formatada, que é só conforto de leitura. Ações: *Copiar*, *Comprovante* (o
texto pronto para anexar num chamado) e **Fixar evidência**.

Duas coisas para não errar aqui:

- **A faixa de cobertura acompanha todo resultado.** Verde quer dizer "o coletor
  esteve conectado 100% do período — o que não está aqui não foi publicado".
  Âmbar lista as lacunas. Sem essa faixa verde, ausência de mensagem **não prova
  nada**: pode ter sido o coletor que estava fora do ar.
- **Mensagem fixada como evidência nunca é apagada** pela retenção, nem por
  idade nem por espaço. Se o comprovante já foi usado num chamado, fixe.

### Retenção — o que dura quanto

| Dado | Padrão | Onde muda |
|---|---|---|
| Mensagens cruas (ledger) | 7 dias, sem teto de MB | `/config` |
| Transições de estado | 7 dias | `/config` |
| Chamadas reconstruídas | 90 dias | `/config` |
| Resumo diário por ramal | 365 dias | `/config` |
| Histórico de conexão do coletor | 1 ano, fixo | — |
| Evidências fixadas | para sempre | — |

O resumo diário é o único histórico longo: ele sobrevive à poda de todo o resto,
e é dele que sai qualquer comparação de meses atrás.

### Quando algo parece errado

- **"não configurado"** no cabeçalho: não há broker cadastrado — vá em `/config`.
- **"sem conexão"**: o coletor não está conseguindo assinar. Veja em `/logs` os
  eventos do módulo `mqtt`; credencial recusada e certificado trocado aparecem
  lá com o motivo.
- **Estado do painel diferente do que o telefone mostra**: o painel reflete o
  que o PBX publicou. Se o publicador parou, o cartão apaga com "sem msg há X" —
  compare com a hora da última mensagem na saúde da ingestão.
