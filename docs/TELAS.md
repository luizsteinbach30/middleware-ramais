# Documento de Telas — Middleware USCall Monitor v2.0

**Versão:** 2.0
**Data:** 2026-05-09
**Audiência:** designers, frontend, QA, suporte

Cada seção descreve: rota, propósito, layout, componentes, dados consumidos, ações, regras de exibição e critérios de aceite.

---

## Convenções globais

- **Tema:** dark mode Tailwind (`bg-gray-900`, `text-gray-200`).
- **Layout:** sidebar fixa à esquerda (256px), header superior, conteúdo central (max-width 1280px com paddings de 24px).
- **Tipografia:** sans-serif do SO, pesos 400/600/700.
- **Atalhos visuais de status:**
  - Verde (`text-green-400`) — online / sucesso.
  - Vermelho (`text-red-400`) — offline / falha.
  - Azul (`text-blue-400`) — disponível (lógico) / informação.
  - Amarelo (`text-yellow-400`) — atenção / indisponível lógico.
  - Cinza (`text-gray-400`) — desconhecido / sem dado.
- **Toasts:** canto superior direito, auto-dismiss 4s.
- **Modais:** backdrop `bg-black/60`, conteúdo `bg-gray-800` rounded-xl.
- **Tabelas:** header `bg-gray-700`, linhas `divide-gray-700`, hover `bg-gray-700/40`.
- **Datas:** exibidas no fuso do navegador, com tooltip mostrando UTC.
- **Tokens sensíveis:** sempre exibidos como `••••••••` com botão "Alterar" que limpa e libera o input.

---

## Mapa de navegação

```
Público:
  /login                       — Login
  /healthz                     — JSON liveness (sem UI)

Autenticado:
  /                            — Dashboard
  /devices                     — Lista de devices
  /devices/{id}                — Detalhe + gráfico
  /collections                 — Histórico de coletas
  /webhook-logs                — Log de webhooks
  /logs                        — Logs do sistema
  /config                      — Configuração
  /system/updates              — Updates / versão
  /account                     — Trocar senha / sair
```

---

## 0. Login — `/login`

**Quando:** usuário não autenticado em qualquer rota protegida.

**Layout:** card central 400px sobre fundo escuro com gradiente sutil. Logo no topo, título "Middleware Monitor".

**Componentes:**
- Campo `usuário` (auto-foco).
- Campo `senha` (com botão olho 👁 para revelar).
- Botão `Entrar`.
- Mensagem de erro em vermelho abaixo do botão.
- Rodapé com versão atual e link "Esqueci minha senha" (apenas instrui contatar admin).

**Dados:**
- `POST /api/auth/login {username, password}` → `{ok}` + cookie de sessão.

**Regras:**
- Após 5 falhas em 10min: bloqueio por 5min com mensagem genérica "Muitas tentativas. Tente novamente mais tarde."
- Após login bem-sucedido: redireciona para `?next=` ou `/`.
- Se senha for `must_change=true` (primeiro acesso): redireciona para `/account?force=1`.

**Critérios de aceite:**
- [ ] Mensagem de erro nunca informa qual campo está incorreto.
- [ ] CSRF token válido por requisição.
- [ ] Cookie `Secure` em produção, `HttpOnly`, `SameSite=Lax`.

---

## 1. Dashboard — `/`

**Propósito:** visão operacional consolidada do servidor.

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│ Dashboard Operacional                       [Atualizar]  │
├────────────┬────────────┬────────────┬──────────────────┤
│ Devices    │ Net Online │ Net Offline│ Latência Média   │
│   123      │     115    │      8     │      4 ms        │
├────────────┼────────────┼────────────┴──────────────────┤
│ Lóg. Avail │ Lóg. Indis │ Latência Máxima               │
│   118      │      5     │      25 ms                    │
├────────────┴────────────┴───────────────────────────────┤
│ Última coleta: 2026-05-09 14:33:21    Webhooks 24h: 144 │
│ Versão atual: 2.0.3 (stable)          Próxima: —        │
│                                                          │
│ Gráfico de devices online/offline (últimas 24h)          │
└──────────────────────────────────────────────────────────┘
```

**Componentes:**
- 6 KPI cards (grid responsivo 4/2/1 colunas).
- Linha de status (última coleta, versão, status do updater).
- Gráfico de linha (Chart.js) com online vs offline ao longo das últimas 24h, agrupamento por 15 min.
- Botão `Forçar Coleta` (admin only) que dispara `POST /api/devices/force-monitor`.

**Dados:**
- `GET /api/dashboard/summary` → `{total, network_online, network_offline, logical_available, logical_unavailable, avg_latency_ms, max_latency_ms, last_collection_at, webhooks_24h, last_webhook_status}`.
- `GET /api/dashboard/timeseries?window=24h` → série para o gráfico.
- `GET /api/system/version` → versão atual e próxima disponível.

**Refresh:** auto a cada 5s (somente cards) e 60s (gráfico).

**Critérios de aceite:**
- [ ] Todos os números recalculam ao vivo sem reload.
- [ ] Card "Próxima versão" só aparece quando há versão > atual no canal.
- [ ] Botão `Forçar Coleta` desabilita por 60s após clique e mostra spinner.

---

## 2. Devices — `/devices`

**Propósito:** lista de todos os ramais monitorados, com filtros e ações.

**Layout:**
```
┌──────────────────────────────────────────────────────────────────┐
│ Devices                                  [Forçar ping] [Exportar]│
├──────────────────────────────────────────────────────────────────┤
│ Filtros: [Status rede ▾] [Status lógico ▾] [Busca por ramal/IP]  │
├──────────────────────────────────────────────────────────────────┤
│ Resumo: USCall ✅ 118  ⚠ 5    Rede 🟢 115  🔴 8                  │
├──────────────────────────────────────────────────────────────────┤
│ Ramal │ IP │ MAC │ Modelo │ USCall │ Rede │ Lat. │ Last seen │ … │
│ ...                                                              │
├──────────────────────────────────────────────────────────────────┤
│        << anterior  Página 1 / 5  próxima >>                     │
└──────────────────────────────────────────────────────────────────┘
```

**Colunas da tabela:**
| Coluna | Descrição | Estilo |
|---|---|---|
| Ramal | nome lógico | bold |
| IP | endereço IPv4 | mono |
| MAC | endereço MAC ou `-` | mono |
| Modelo | fabricante detectado ou `-` | – |
| USCall | `disponivel`/`indisponivel`/`-` | azul/amarelo |
| Rede | `online`/`offline`/`-` | verde/vermelho |
| Latência | em ms ou `-` | – |
| Last seen | timestamp | tooltip UTC |
| Last ping | timestamp | tooltip UTC |
| Ações | `Detalhes`, `Forçar ping` | botões pequenos |

**Filtros:**
- Status de rede: todos / online / offline / desconhecido.
- Status lógico: todos / disponível / indisponível.
- Busca textual (nome ou IP), debounce 300ms.

**Ações:**
- `Forçar ping` (linha) → `POST /api/devices/{id}/refresh`. Mostra spinner inline e atualiza linha.
- `Forçar coleta` (topo) → `POST /api/devices/force-monitor`.
- `Exportar` → CSV com filtros aplicados.
- Click em `Ramal` ou em "Detalhes" → `/devices/{id}`.

**Dados:**
- `GET /api/devices?status=&logical=&search=&page=&size=` paginado (50/página).

**Refresh:** 5s; pausa quando modal aberto.

**Critérios de aceite:**
- [ ] Filtros são preservados na URL (querystring).
- [ ] Tabela vazia mostra estado "Sem devices ainda. Configure o USCall em /config".
- [ ] Exportar CSV gera arquivo com timestamp no nome.

---

## 3. Detalhe do device — `/devices/{id}`

**Propósito:** visão detalhada e histórica de um device.

**Layout:**
```
┌──────────────────────────────────────────────────────────────────┐
│ Ramal 3660                                              [Voltar] │
├──────────────────────────────────────────────────────────────────┤
│ Status atual                                                     │
│  Lógico: disponível        Rede: online                          │
│  IP: 10.20.30.40           MAC: aa:bb:cc:dd:ee:ff                │
│  Modelo: Yealink           Último ping: 14:33:21 (3ms)           │
├──────────────────────────────────────────────────────────────────┤
│ Histórico de Latência          [24h] [7d] [30d] [Custom]         │
│  ┌─ Chart.js linha ────────────────────────────────────────┐    │
│  └────────────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────────────┤
│ Eventos recentes (últimos 50 pings)                              │
│  Tabela: timestamp | online | latência                           │
└──────────────────────────────────────────────────────────────────┘
```

**Componentes:**
- Card de status atual.
- Seletor de janela temporal (24h padrão).
- Gráfico de linha com latência ms.
- Indicadores de "queda" (online=false) marcados com pontos vermelhos.
- Tabela paginada de pings.
- Botões `Forçar ping` e `Editar notas` (admin).

**Dados:**
- `GET /api/devices/{id}`
- `GET /api/devices/{id}/history?from=&to=&granularity=auto`
- `GET /api/devices/{id}/pings?page=&size=`

**Critérios de aceite:**
- [ ] Mudança de janela atualiza gráfico sem reload.
- [ ] Quando não há pings, exibe placeholder "Sem dados ainda".
- [ ] Granularidade `auto` agrega: ≤24h por minuto, ≤7d por 5min, ≤30d por hora.

---

## 4. Histórico de coletas — `/collections`

**Propósito:** auditoria das coletas brutas do USCall.

**Layout (split 50/50):**
```
┌─────────────────────────────┬───────────────────────────────────┐
│ Lista de coletas            │ Visualizador JSON                 │
│ Filtros: [data ▾] [tipo ▾]  │                                   │
│ ┌─────────────────────────┐ │ {                                 │
│ │ 2026-05-09 14:30:01 [v] │ │   "ramal": "3660",                │
│ │ 2026-05-09 14:00:01 [v] │ │   "status": "disponivel",         │
│ │ 2026-05-09 13:30:01 [v] │ │   ...                             │
│ │ ...                     │ │ }                                 │
│ └─────────────────────────┘ │                                   │
│ << 1 / 12 >>                │ [Baixar JSON] [Copiar]            │
└─────────────────────────────┴───────────────────────────────────┘
```

**Filtros:**
- Tipo (`extensions`, `devices`, `results`).
- Intervalo de datas (date-range picker).
- Busca por hash ou ID.

**Componentes:**
- Lista paginada (50/página) com timestamp, tipo, tamanho do payload, hash truncado.
- Visualizador JSON com syntax highlighting (lib leve, ex: `prism.js`).
- Botões `Baixar JSON` e `Copiar`.

**Dados:**
- `GET /api/collections?type=&from=&to=&page=&size=`.
- `GET /api/collections/{id}` → payload completo.

**Critérios de aceite:**
- [ ] Selecionar item da lista carrega o JSON sem recarregar a página.
- [ ] Filtros persistem na URL.
- [ ] Quando lista vazia, exibe orientação para configurar a integração.

---

## 5. Webhook logs — `/webhook-logs`

**Propósito:** histórico de chamadas de webhook enviadas pelo middleware.

**Layout:**
```
┌──────────────────────────────────────────────────────────────────┐
│ Webhook Logs                                                     │
│ Filtros: [tipo ▾] [status ▾] [data ▾]                            │
│ Ações:   [Testar extensions] [Testar devices] [Testar results]   │
├──────────────────────────────────────────────────────────────────┤
│ Data | Tipo | HTTP | Tempo | Status | Tentativa | Ações          │
│ ...                                                              │
├──────────────────────────────────────────────────────────────────┤
│ << 1 / 8 >>                                                      │
└──────────────────────────────────────────────────────────────────┘
```

**Colunas:**
- Data (UTC convertida).
- Tipo (`extensions`/`devices`/`results`/`test`/`system`); destacado quando `payload.test=true`.
- HTTP status (badge colorido por faixa).
- Duração (ms).
- Status (`Sucesso` verde / `Falha` vermelho).
- Tentativa (`1/3`, `2/3`, `3/3` quando houve retry).
- Ações: `Ver payload`, `Ver resposta`, `Reenviar` (admin only).

**Modais:**
- `Ver payload`: mostra JSON enviado (response/request) com botão copiar.
- `Ver resposta`: corpo HTTP completo + headers principais.

**Dados:**
- `GET /api/webhook-events?type=&status=&from=&to=&page=&size=`.
- `POST /api/webhooks/test/{event_type}` para testar.
- `POST /api/webhook-events/{id}/replay` para reenviar.

**Critérios de aceite:**
- [ ] `Reenviar` cria novo evento marcado `is_replay=true`.
- [ ] Os 3 botões de teste enviam payload com `test=true` e aparecem destacados na tabela.
- [ ] Falhas mostram tooltip com mensagem de erro.

---

## 6. Logs do sistema — `/logs`

**Propósito:** consultar logs estruturados gerados pela aplicação.

**Layout:** tabela com filtros laterais.

**Filtros:**
- Nível (`DEBUG`/`INFO`/`WARN`/`ERROR`).
- Módulo (`scheduler`, `monitor`, `webhook`, `updater`, `auth`, ...).
- Intervalo de datas.
- Busca textual no `message`.

**Colunas:**
- Timestamp.
- Nível (badge colorido).
- Módulo (chip).
- Mensagem (truncada com expand).
- Botão `Ver contexto` que abre modal com o JSON `context`.

**Dados:**
- `GET /api/logs?level=&module=&from=&to=&search=&page=&size=`.

**Critérios de aceite:**
- [ ] Linhas com `ERROR` ficam destacadas (background levemente vermelho).
- [ ] Modal de contexto formata JSON com indent 2.
- [ ] Auto-refresh opcional (toggle), padrão desligado.

---

## 7. Configuração — `/config`

**Propósito:** editar todos os parâmetros operacionais.

**Estrutura em seções (cards verticais):**

### 7.1 Identificação do cliente
- Campo `client_code` (obrigatório, slug).

### 7.2 Integração USCall
- Campo `uscall_host` (texto, sem `https://`).
- Campo `uscall_token` (mascarado; botão `Alterar` libera input).
- Toggle `verify_ssl` (default: ligado).
- Botão `Testar conexão` → `POST /api/uscall/test`. Mostra resultado inline (verde/vermelho) com latência e código HTTP.

### 7.3 Intervalos de coleta
- Inputs numéricos (em segundos) com mínimo 10:
  - `extensions_interval_seconds`
  - `devices_interval_seconds`
  - `results_interval_seconds`
- Tooltip explicando o que cada um coleta.

### 7.4 Monitoramento de rede
- `ping_timeout_ms` (default 1000).
- `ping_concurrency` (default 20, máximo 200).
- `device_ping_retention_days` (default 30).

### 7.5 Webhooks (extensions / devices / results)
Cada bloco repete:
- Toggle `enabled`.
- Campo `url` (validação de URL).
- Campo `token` mascarado com botão `Alterar`.
- Botão `Testar` → `POST /api/webhooks/test/{type}`.
- Status do último envio (`OK há 3min`, `Falhou há 12min`).

### 7.6 Retenção e limpeza
- `webhook_log_retention_days` (default 30).
- `collection_retention_days` (default 90).
- `system_log_retention_days` (default 14).
- Botão `Limpar agora` (admin only) com confirmação.

**Ações da página:**
- `Salvar configuração` (sticky no rodapé) → `PUT /api/config` enviando apenas campos modificados.
- `Recarregar` descarta alterações e busca config atual.

**Regras:**
- Tokens só são enviados ao backend quando o usuário marcou "alterar".
- Validações no cliente + erro inline por campo.
- Salvar dispara toast de sucesso e atualiza timestamp `updated_at`.

**Critérios de aceite:**
- [ ] Sair da página com alterações não salvas exibe alerta.
- [ ] Tokens nunca aparecem em texto plano após salvos (nem no DOM).
- [ ] Erros do backend (ex: host inválido) marcam o campo correspondente.

---

## 8. Updates / versão — `/system/updates`

**Propósito:** gerenciar atualizações do middleware.

**Layout:**
```
┌──────────────────────────────────────────────────────────────────┐
│ Atualizações                                                     │
├──────────────────────────────────────────────────────────────────┤
│ Versão atual: 2.0.3                                              │
│ Canal: [stable ▾]   Auto-update: [✓]                             │
│ Último check: 2026-05-09 14:00:00                                │
│ Próxima versão disponível: 2.1.0 (stable) — Notas               │
│   [Verificar agora] [Atualizar agora]                            │
├──────────────────────────────────────────────────────────────────┤
│ Histórico de atualizações                                        │
│ Data | De | Para | Canal | Status | Duração | Detalhes           │
│ ...                                                              │
└──────────────────────────────────────────────────────────────────┘
```

**Componentes:**
- Card de status com versão atual, canal, último check, próxima versão (se houver).
- Botão `Verificar agora` (rate-limit 1/min).
- Botão `Atualizar agora` (admin only) — confirma, mostra progresso (download → verificação → migrate → restart).
- Toggle `Auto-update`.
- Seletor `Canal`.
- Tabela de `update_history` com status e link para detalhes (modal com erro completo se falhou).

**Dados:**
- `GET /api/system/version`
- `POST /api/system/check-update`
- `POST /api/system/update` (admin)
- `PATCH /api/system/update-settings` (canal, auto-update)
- `GET /api/system/update-history?page=&size=`

**Critérios de aceite:**
- [ ] Durante update, a UI mostra progresso e bloqueia ações conflitantes.
- [ ] Em rollback automático, status é claramente sinalizado.
- [ ] Mudar canal não dispara update sozinho — o próximo check decide.

---

## 9. Conta — `/account`

**Propósito:** gerenciar a sessão do próprio usuário.

**Componentes:**
- Card "Trocar senha" com `senha atual`, `nova senha`, `confirmação`.
  - Validação: mínimo 12 caracteres, com letras e números.
  - `POST /api/auth/change-password`.
- Card "Sessão atual" com IP, user-agent, criado em.
- Botão `Sair` → `POST /api/auth/logout`.

**Regras:**
- Quando acessada com `?force=1` (primeiro login), todas as outras ações ficam desabilitadas até trocar a senha.
- Após troca, sessão é renovada e usuário é redirecionado para `/`.

**Critérios de aceite:**
- [ ] Senha errada não revela qual campo falhou.
- [ ] Política de senha mostrada de forma clara antes de submeter.

---

## 10. Erros e estados

### 10.1 Página de erro 404
- Card centralizado com ilustração simples e link "Voltar para o Dashboard".

### 10.2 Página de erro 500
- Card centralizado com ID do erro (gerado pelo middleware) que pode ser referenciado no suporte.

### 10.3 Banner global de status
- Quando `/api/system/readyz` falhar (DB indisponível, scheduler parado), exibe banner amarelo no topo do layout: "Serviço degradado — coleta pausada. Verifique /logs."

### 10.4 Estado offline (sem rede para o backend)
- Toast persistente "Conexão com o servidor perdida. Tentando reconectar…" com retry exponencial.

---

## 11. Acessibilidade e responsividade

- Todos os botões têm `aria-label` quando o ícone é o único conteúdo.
- Foco visível em todos os elementos interativos (Tailwind `focus:ring`).
- Contraste mínimo AA (verificar combinações `gray-200` em `gray-900`).
- Em telas <768px, sidebar vira drawer aberto por botão hambúrguer.
- Tabelas viram cards empilhados em mobile.

---

## 12. Resumo dos endpoints consumidos pela UI

| Tela | Método | Endpoint |
|---|---|---|
| Login | POST | `/api/auth/login` |
| Login (logout) | POST | `/api/auth/logout` |
| Dashboard | GET | `/api/dashboard/summary` |
| Dashboard | GET | `/api/dashboard/timeseries` |
| Dashboard / Updates | GET | `/api/system/version` |
| Devices (lista) | GET | `/api/devices` |
| Devices (forçar) | POST | `/api/devices/force-monitor` |
| Devices (refresh um) | POST | `/api/devices/{id}/refresh` |
| Device detail | GET | `/api/devices/{id}` |
| Device detail | GET | `/api/devices/{id}/history` |
| Device detail | GET | `/api/devices/{id}/pings` |
| Collections | GET | `/api/collections` |
| Collections | GET | `/api/collections/{id}` |
| Webhook logs | GET | `/api/webhook-events` |
| Webhook logs (teste) | POST | `/api/webhooks/test/{event_type}` |
| Webhook logs (replay) | POST | `/api/webhook-events/{id}/replay` |
| Logs | GET | `/api/logs` |
| Config | GET | `/api/config` |
| Config | PUT | `/api/config` |
| Config (USCall test) | POST | `/api/uscall/test` |
| Updates | POST | `/api/system/check-update` |
| Updates | POST | `/api/system/update` |
| Updates | PATCH | `/api/system/update-settings` |
| Updates | GET | `/api/system/update-history` |
| Account | POST | `/api/auth/change-password` |

---

## 13. Critérios de aceite — UI como um todo

- [ ] Nenhum endpoint protegido aceita request sem cookie de sessão.
- [ ] Nenhum token (USCall ou webhook) chega ao DOM em texto plano.
- [ ] Todas as mutações exibem feedback (toast ou inline) em <1s.
- [ ] Todos os filtros relevantes refletem na URL.
- [ ] Páginas degradadas (sem dados, falha de backend) têm estado vazio claro.
- [ ] Tema escuro consistente em todas as telas; sem flashes de tema claro.

---

## 14. Configurador de Ramais (v2.2.0)

Sidebar ganha bloco **CONFIGURADOR DE RAMAIS** (sob border-top) com 2 entries:
**Ambientes** e **Relatórios**. Highlight ativo cobre as páginas filhas.

### 14.1 Ambientes (`/extension-configurator/environments`)
- Grid responsivo de cards (1/2/3 colunas conforme breakpoint).
- Cada card: nome, modelo do telefone, contagem de ramais, timestamp atualizado.
- Botão "+ Novo ambiente" abre modal com inputs (Nome + dropdown de modelos).
- Empty state quando sem ambientes.

### 14.2 Detalhe do ambiente (`/extension-configurator/environments/{id}`)
- Header sticky com botão **voltar pill** (chevron + "Ambientes"), nome do
  ambiente (truncado), subtítulo "modelo · N ramais", link **⚙ Config padrão**
  e botões **Salvar planilha**, **Aplicar selecionados (N)** (azul, com shadow,
  desabilitado quando N=0) e **Aplicar tudo**.
- Pills de status no topo: aplicado / desatualizado / pendente / erro,
  com contadores em `tabular-nums`.
- Planilha Jspreadsheet CE (`dist/index.min.js`) em wrapper com card/sombra.
  Colunas (esquerda → direita):
  - `id` (hidden), `✓` (checkbox de seleção), `IP`, `Ramal`, `Nome visível`,
    `User auth`, `Senha SIP`, `Servidor SIP`, `Nº abreviado`, **`Status`**
    (readonly), **`Modelo`** (readonly), **`MAC`** (readonly),
    **`Última aplic.`** (readonly), **`Erro`** (readonly).
- Header sticky uppercase, hover de linha, foco azul no editor, readonly
  com tom diferenciado.
- **Smart autofill numérico** (v2.2.1): arrastar o canto da seleção
  detecta prefixo + sufixo numérico (`HOST01` → `HOST02`,
  `192.168.0.10` → `192.168.0.11`) — complementa o autofill nativo do
  Jspreadsheet que só funciona com número puro.
- **Aviso de senha SIP HTEK** antes de aplicar: alerta quando a senha
  tem >25 chars ou contém chars fora do safe charset do firmware.
- Barra de botões secundária (grupo segmentado pill):
  - **marcar todos** (azul) · **desmarcar** (cinza) · **só erros/pendentes**
    (amarelo) com ícones SVG.
  - Toggle **Forçar reaplicação** (track/thumb estilo iOS) que destaca em
    azul quando ativo.
- Painel "Execução em andamento" aparece após "Aplicar":
  - Summary: contagem por stage (pending/ping/send/done/error).
  - Lista de linhas com IP, ramal, stage, mensagem.
  - Botão "Cancelar" disponível enquanto não finalizou.
  - Polling a cada 1.5s. Stages intermediários (ping/send) mostram
    *"aplicando…"* na coluna Status (em vez de "desatualizado", que
    causava flicker incorreto).
  - Ao finalizar, recarrega a planilha pra mostrar status persistido.
- **Device actions (v2.7.0)** — ações remotas nos telefones homologados,
  controladas por capabilities (`GET .../capabilities`): vendor sem ação
  homologada não mostra nada (Intelbras hoje).
  - Coluna **`⋮`** (readonly, só em linha salva e com capability): abre menu
    popover ancorado na célula com as ações do vendor:
    - **🔧 Normalizar telefone** — volume no máximo + DND desligado
      (desfaz mute/DND ativado por operador). `confirm()` → POST da ação →
      toast com resultado (avisa quando o aparelho reinicia, ex.: HTEK).
    - **⚠ Alterar IP…** (quando `set_ip` homologado) — abre modal perigoso
      (ring vermelho): input de **novo IP** + confirmação **digitando o IP
      atual**; botão só habilita com IP atual correto e novo IP diferente.
      Backend rejeita `confirm_ip` errado com 400.
  - Botão **🔧 Normalizar telefones** no header (verde, só com capability
    `normalize`): confirm → `POST .../actions/normalize` devolve
    `{run_id, total}` e o run roda em background no servidor.
  - Painel "**Normalização em andamento**" (gêmeo do painel de aplicação):
    summary por stage (pendente/executando/ok/erro), lista IP · ramal ·
    stage · mensagem, polling 1.5s em `GET /action-runs/{run_id}/live`;
    404 do live (run expirou da memória) encerra o acompanhamento com toast
    — a auditoria fica persistida em `device_action_events`.

### 14.3 Config padrão (`/extension-configurator/environments/{id}/config`)
- Header sticky com botão **voltar pill** que mostra o **nome do ambiente**
  como label, título "Config padrão — {nome}", subtítulo "Modelo: X ·
  servidor SIP é definido por linha na planilha", botão **Salvar**.
- Form em cards (`div + h3`, não `fieldset/legend` — o reset CSS do
  Tailwind deslocava as legends para fora das bordas):
  - **SIP**: register expiration (servidor SIP **removido** da tela em
    v2.2.1 — vem só da coluna da planilha).
  - **Credencial atual do aparelho**: `web_user`/`web_password` (usados
    pra autenticar no upload — não vão no XML).
  - **Nova credencial** (opcional): `nova_web_user`/`nova_web_password` —
    só vão no XML se preenchidos; vazios = não muda a senha do aparelho.
  - **Validação**: checkbox `validar_conectividade` (ICMP ping antes de
    send).
  - **Avançadas (Intelbras)** — só visível quando o modelo é Intelbras:
    `menu_password`, `keylock_password`, `keylock_enable` (0/1/2),
    `keylock_timeout` (s).
  - **Function Keys (HTEK)** / **DSS Keys (Intelbras)** — editor dinâmico
    com linhas que têm: tecla (LineKey1..4), tipo (Desabilitada/Linha SIP/
    Discagem rápida/BLF), label, account (HTEK força 0, oculta o campo),
    e *Valor* (`vem da planilha` selecionando uma coluna ou `fixo` com
    string livre). Botão **+ adicionar tecla** e **✕** para remover.
- Botão "Salvar" no header → PUT em `/api/extension-configurator/environments/{id}`.
- **UX**: após criar um ambiente novo, o usuário cai direto nesta tela
  (em vez do detail) para configurar credencial e function keys antes da
  planilha.

### 14.4 Relatórios (`/extension-configurator/runs`)
- Tabela do histórico de execuções: Ambiente, Início, Fim, Total, OK, Falha,
  Operador, Forçado, **Detalhes**.
- Cada linha é clicável e tem coluna **abrir →** que leva ao detalhe
  do run.
- Empty state quando sem execuções.

### 14.5 Detalhe do relatório (`/extension-configurator/runs/{run_id}`) — v2.2.1
- Header sticky com botão **voltar pill** ("Relatórios"), título
  "Relatório #{id} — {nome do ambiente}", subtítulo com modelo e data de
  início, link **Ir para o ambiente**.
- Linha de **5 cards** com cifras em `tabular-nums`:
  Total / OK (verde) / Falha (vermelho) / Duração ("Xm Ys") / Operador.
- Tabela "Linhas do ambiente" — snapshot **atual** das linhas
  (storage só guarda agregados por run, não snapshot por linha):
  - IP, Ramal, Nome, **Status** (badge pill colorido: aplicado / desatualizado
    / pendente / erro), Modelo, MAC, Última aplicação, Erro.
- Empty state se o ambiente foi removido depois.

### 14.6 Endpoints API consumidos

| Recurso | Método | Path |
|---|---|---|
| Phone models | GET | `/api/extension-configurator/phone-models` |
| Environments | GET | `/api/extension-configurator/environments` |
| Environments | POST | `/api/extension-configurator/environments` |
| Environment detail | GET | `/api/extension-configurator/environments/{id}` |
| Environment update | PUT | `/api/extension-configurator/environments/{id}` |
| Environment delete | DELETE | `/api/extension-configurator/environments/{id}` |
| Lines bulk save | PUT | `/api/extension-configurator/environments/{id}/lines` |
| Apply | POST | `/api/extension-configurator/environments/{id}/apply` |
| Runs (por env) | GET | `/api/extension-configurator/environments/{id}/runs` |
| Runs (geral) | GET | `/api/extension-configurator/runs` |
| Run detail (v2.2.1) | GET | `/api/extension-configurator/runs/{run_id}/detail` |
| Run live | GET | `/api/extension-configurator/runs/{run_id}/live` |
| Run cancel | POST | `/api/extension-configurator/runs/{run_id}/cancel` |
| Capabilities (v2.7.0) | GET | `/api/extension-configurator/environments/{id}/capabilities` |
| Ação por linha (v2.7.0) | POST | `/api/extension-configurator/environments/{id}/lines/{line_id}/actions/{action}` |
| Normalizar em massa (v2.7.0) | POST | `/api/extension-configurator/environments/{id}/actions/normalize` |
| Action run live (v2.7.0) | GET | `/api/extension-configurator/action-runs/{run_id}/live` |
| Auditoria de ações (v2.7.0) | GET | `/api/extension-configurator/environments/{id}/action-events` |
