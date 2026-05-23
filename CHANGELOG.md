# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/) · SemVer.

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
