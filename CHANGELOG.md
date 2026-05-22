# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/) · SemVer.

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
