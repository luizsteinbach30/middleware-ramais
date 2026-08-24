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
- **Sidebar recolhível (v2.7.0):** botão circular na borda da sidebar (ou
  <kbd>Ctrl+B</kbd>) alterna entre 256px e um **trilho de 64px só com ícones**.
  Serve principalmente à planilha do Configurador, que é larga e tinha as
  últimas colunas (incluindo **Erro**) cortadas. O estado fica em
  `localStorage['mm.sidebar']` e é aplicado no `<html>` por script inline no
  `base.html` **antes do primeiro paint** (evita o "pulo" ao carregar). Ao
  alternar, dispara um `resize` para o Jspreadsheet remedir a largura.

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
  /mqtt-painel                 — Painel ao vivo dos ramais (MQTT)
  /mqtt-messages               — Mensagens MQTT (consulta do ledger)
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
- Card de status com versão atual, canal, último check, próxima versão (se houver)
  e a linha **"Próxima verificação: HH:MM (fuso) · dias"**.
- Botão `Verificar agora` (rate-limit 1/min).
- Botão `Atualizar agora` (admin only) — confirma, mostra progresso (download → verificação → migrate → restart).
- **Card "Verificação automática" (v2.7.0)** — controles **reais** e persistidos:
  - toggle **Verificar automaticamente** (pausa a checagem periódica; o botão
    "Verificar agora" continua funcionando);
  - **Canal** `stable` / `beta` — passa a valer da tela; o `.env`
    (`APP_UPDATE_CHANNEL`) vira apenas fallback de primeira execução;
  - **Horário** hora:minuto, no **fuso local do servidor** (exibido ao lado);
  - **Dias da semana** em chips — permite janela de manutenção (ex.: só
    seg–sex, para não receber aviso às vésperas do fim de semana). Salvar sem
    nenhum dia é bloqueado na UI (para pausar existe o toggle).
  - Salvar **reagenda o job na hora**, sem reiniciar o serviço.
- Tabela de `update_history` com status e link para detalhes (modal com erro completo se falhou).

> **Contrato explícito:** o agendamento **apenas verifica e avisa** — nunca
> instala sozinho (decisão de 2026-08-03). A instalação continua exigindo o
> clique em "Atualizar agora". A API devolve `installs_automatically: false`.
> Antes da v2.7.0 o seletor de canal e o toggle existiam mas eram
> **decorativos**: sem handler, sem persistência, e o `auto_update` exibido era
> um literal `True` no código.

**Dados:**
- `GET /api/system/version`
- `GET /api/system/update-settings`
- `PUT /api/system/update-settings` (admin + CSRF)
- `POST /api/system/check-update`
- `POST /api/system/update` (admin)
- `GET /api/system/update-history?page=&size=`

**Critérios de aceite:**
- [ ] Durante update, a UI mostra progresso e bloqueia ações conflitantes.
- [ ] Em rollback automático, status é claramente sinalizado.
- [ ] Mudar canal não dispara update sozinho — o próximo check decide.
- [x] Desligar a verificação **realmente remove o job** do scheduler (não só
      esconde a UI) e `GET /version` passa a reportar `auto_update: false`.

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
- **Seleção múltipla** (clique no botão de seleção do card, que fica realçado):
  alimenta a exportação XLSX/PDF (sem seleção, exporta os visíveis) e o botão
  **Apagar selecionados** (v2.7.0).
- **Apagar selecionados** (v2.7.0) — botão vermelho que **só aparece quando há
  seleção** (ação destrutiva não fica a um clique no estado normal). Abre modal
  com a contagem de ambientes/ramais, a **lista do que será apagado** e
  confirmação digitando `APAGAR`. Apaga em série pelo `DELETE` por ambiente,
  reporta parciais (ex.: "3 apagados" + "Falha em 1: …") e recarrega a lista.
- Cada card: nome, modelo do telefone, contagem de ramais, timestamp atualizado.
- Botão "+ Novo ambiente" abre modal com inputs (Nome + dropdown de modelos).
- Empty state quando sem ambientes.

### 14.2 Detalhe do ambiente (`/extension-configurator/environments/{id}`)
- Header sticky enxuto: botão **voltar pill** (chevron + "Ambientes"), nome do
  ambiente (truncado) + lápis de renomear, subtítulo "modelo · N ramais",
  indicador de autosave e as **pills de status** (aplicado / desatualizado /
  pendente / erro, contadores em `tabular-nums`).
- **Barra de ferramentas agrupada (v2.7.0).** Antes os 13 controles estavam
  espalhados entre o header e a faixa de ajuda, com alturas e fontes
  diferentes (`text-xs` vs `text-sm`, pill vs botão). Agora **todos** usam a
  mesma classe `.ec-btn` (30px de altura, 12px de fonte) e ficam numa única
  barra, agrupados por finalidade — da esquerda (preparar) para a direita
  (executar), cada grupo com rótulo em caixa alta e separador vertical:
  - **Ambiente** — ⚙ Config padrão · ⧉ Duplicar · ⤴ Exportar
  - **Planilha** — 💾 Salvar planilha · 👁 Pré-visualizar
  - **Seleção** — Marcar todos · Desmarcar · Pendentes
  - **Opções** — toggles Forçar reaplicação · Monitorar ping
  - **Aplicar nos telefones** (empurrado para a direita) — Aplicar
    selecionados (N) *(único botão de fundo azul da tela)* · Aplicar tudo ·
    🔧 Normalizar telefones
  - Cor só codifica intenção: azul = informação/seleção, amarelo = atenção,
    verde = ação de recuperação, azul sólido = ação primária.
  - O texto de ajuda das colunas virou um `<details>` **"Como usar a planilha
    e as colunas"**, recolhido por padrão.
  - Com a **sidebar recolhida** (Ctrl+B) a barra inteira cabe em uma linha.
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
    `normalize`): mesma semântica de seleção do "Aplicar" — com linhas
    marcadas na coluna ✓ normaliza **só as selecionadas** (`selected_ids`
    no body), sem seleção normaliza todas com IP. Confirm →
    `POST .../actions/normalize` devolve `{run_id, total}` e o run roda em
    background no servidor.
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
- **Fluxo de criação (v2.7.0)** — ambiente novo **do zero ou clonado** cai
  primeiro nesta tela (para revisar credencial, teclas etc.) e, ao salvar,
  segue **automaticamente para a planilha de ramais**:
  - quem cria/duplica manda o usuário para `…/config?novo=1`;
  - com `novo=1`, o botão vira **"Salvar e cadastrar ramais →"** e o save
    redireciona para `/extension-configurator/environments/{id}`;
  - **sem** o marcador (entrar pela engrenagem para editar um ambiente já
    existente) o comportamento é o de sempre: salva, mostra o toast e
    permanece na tela.

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

---

## 15. Coletor MQTT (v2.8.0)

### 15.1 Consulta do ledger — `/mqtt-messages`

**Propósito.** Responder a uma pergunta operacional específica: *"essa mensagem
foi publicada?"*. O serviço que publica no broker não registra os próprios
envios, então esta tela é a única fonte da prova. Fica no menu em **Coletor
MQTT**, abaixo do Configurador de Ramais.

**Layout.**

1. **Cabeçalho** — estado do coletor (`coletando · N msg/min`, `sem conexão` ou
   `não configurado`, este último com link para `/config`), botão *Ao vivo*
   (polling de 3 s, prepend das novas com realce) e *Atualizar*.
2. **Filtros** — período em atalhos (15 min / 1 h / 24 h / 7 dias / intervalo à
   mão com data e hora), tópico com curingas (`+`/`#`, com sugestão dos tópicos
   assinados), ramal, texto no conteúdo e "só evidências".
3. **Faixa de cobertura** — acompanha todo resultado:
   - verde: "coletor conectado durante 100% do período — o que não está aqui não
     foi publicado";
   - âmbar: percentual + lista das lacunas (início, duração, motivo);
   - âmbar: "sem histórico do coletor neste período — a ausência **não prova**
     que nada foi publicado".
4. **Tabela** — recebida em (fuso do navegador), tópico, ramal, prévia do
   conteúdo (com selo *evidência* quando fixada) e ações (ver / baixar
   comprovante). Rodapé com "N exibidas de M no período" e *Carregar mais
   antigas* (cursor `before_id`).
5. **Modal da mensagem** — metadados (registro, recebida, evento no PBX, tópico,
   QoS/retained/binário/truncada, broker), abas **Formatado** e **Como
   recebido** (o payload cru é a prova; o formatado é conforto), e as ações
   *Fixar evidência*, *Copiar* e *Comprovante*.

**Regras de exibição.**

- Filtro de tópico inválido devolve erro explicado em português (ex.: "o `#` só
  pode aparecer no fim do filtro"), não o código da API.
- Contagem com curinga no meio do filtro pode parar no teto de varredura; nesse
  caso a tela mostra "mais de N" em vez de um total exato que seria mentira.
- Mensagem fixada como evidência é imune à retenção — o selo existe para o
  operador saber disso sem abrir a mensagem.

**Critérios de aceite.**

- Consultar um intervalo de 15 minutos com 2.000 mensagens responde em menos de
  1 s e mostra a cobertura correspondente.
- Fixar uma mensagem e rodar a retenção com corte no futuro mantém a mensagem.
- O comprovante baixado traz hora local e UTC, tópico e o payload como recebido.

### 15.2 Painel ao vivo — `/mqtt-painel`

**Propósito.** Responder *"o que está acontecendo agora"*. É a primeira entrada
do menu **Coletor MQTT**, acima de Mensagens. Onde a tela de Mensagens serve à
prova (passado), esta serve à operação (presente): o estado vem do instante em
que a mensagem chegou do broker, não do ciclo de coleta REST.

**Layout.**

1. **Cabeçalho** — estado do coletor (`coletando · N msg/min`, `sem tráfego` ou
   `não configurado`, com link para `/config`) e botão *Pausar/Retomar*
   (atualização a cada 2,5 s).
2. **Contadores por estado** — Todos, Disponível, Tocando, Discando, Em conversa,
   Indisponível. Clicar filtra a grade; clicar de novo desliga o filtro. O card
   "Não reconhecido" só aparece quando existe algum — card zerado permanente vira
   ruído.
3. **Saúde da ingestão** — mensagens/min, ramais acompanhados, fila de gravação,
   descartadas (em vermelho enquanto for > 0: descarte é prova perdida), atraso
   médio do PBX e hora da última mensagem. Quando não há broker cadastrado ou o
   coletor não está rodando, uma faixa âmbar diz que a grade abaixo é o último
   estado conhecido, **não** o de agora.
4. **Grade dos ramais** — um cartão por ramal, colorido pelo estado, com o número
   da outra ponta, há quanto tempo está nesse estado e o estado de rede vindo do
   device. Ramal cujo publicador parou de falar (mais de 2 min) aparece apagado
   com "sem msg há X". O número do ramal é link para as mensagens cruas dele
   (`/mqtt-messages?ramal=…`, janela de 1 h). Campo de busca por ramal ou número.
5. **Últimas transições** — a fita do que mudou (ramal, estado, número, hora),
   mais recente primeiro. Só transições: repetição do mesmo estado não entra.

**Regras de exibição.**

- Ramal sem device correspondente aparece na grade assinalado como "sem device":
  o payload MQTT não traz IP nem MAC, então quem cria o telefone continua sendo a
  coleta REST — ramal novo no PBX aparece aqui antes de existir como device, e
  isso é informação, não erro.
- `Tocando`, `Ocupado` e `Discando` significam ramal **registrado**; só
  `Indisponivel` é vermelho, porque só ele indica problema.

### 15.3 Configuração do coletor — `/config`

Seção **Coletor de mensagens MQTT** (permanece na tela de Configuração): campo
único de endereço com *Descobrir conexão*, credenciais opcionais, relatório da
sonda passo a passo, confirmação da impressão digital do certificado quando ele
não é assinado por CA conhecida, escolha dos tópicos a partir do que existe no
broker, e retenção do ledger (dias + teto opcional em MB, com o volume atual).

### 15.4 Chamadas — `/mqtt-chamadas`

**Propósito.** O PBX não publica chamadas: publica o estado de cada ramal. Esta
tela mostra a chamada **reconstruída** dessa sequência — quem falou com quem,
quanto tocou, quanto durou e como terminou. Menu **Coletor MQTT**, entre Painel
ao vivo e Mensagens.

**Layout.**

1. **Filtros** — período em atalhos (1 h / 24 h / 7 dias / intervalo à mão),
   ramal e outra ponta (ambos por trecho), direção (recebidas / feitas) e
   resultado (atendidas / perdidas / não atenderam / em curso).
2. **Nota fixa acima da tabela** — cada linha é **uma ponta** da chamada: uma
   ligação entre dois ramais aparece duas vezes, e um grupo de captura aparece
   uma vez por ramal tocado. É a leitura errada mais provável da tabela, então
   o aviso não é escondido em ajuda.
3. **Tabela** — início, ramal, direção, outra ponta, resultado, tempo de toque e
   tempo de conversa. Rodapé com a contagem e *Carregar mais*.
4. **Exportar CSV** — mesmo filtro da tela, hora local e separador que o Excel
   em português entende.

**Regras de exibição.**

- Chamada `em curso` não tem duração e é rotulada como tal — não entra no
  resumo diário, que só conta o que terminou.
- Numa ligação interna o PBX só manda o número para quem **recebe**; a ponta que
  originou aparece sem a outra ponta, e isso é o dado real, não falha da tela.
- No detalhe do device (`/devices/{id}`) a seção **Telefonia** mostra o mesmo
  para aquele ramal: resumo do dia e as últimas chamadas.

### 15.5 Endpoints API consumidos

| Recurso | Método | Path |
|---|---|---|
| Estado do coletor | GET | `/api/mqtt/status` |
| Estado ao vivo dos ramais | GET | `/api/mqtt/live` |
| Brokers (CRUD) | GET/POST/PUT/DELETE | `/api/mqtt/brokers[/{id}]` |
| Descoberta do endpoint | POST | `/api/mqtt/discover` |
| Amostragem de tópicos | POST | `/api/mqtt/sniff` |
| Busca no ledger | GET | `/api/mqtt/messages` |
| Detalhe da mensagem | GET | `/api/mqtt/messages/{id}` |
| Fixar evidência | POST | `/api/mqtt/messages/{id}/pin` |
| Comprovante (texto) | GET | `/api/mqtt/messages/{id}/comprovante` |
| Cobertura do período | GET | `/api/mqtt/coverage` |
| Chamadas reconstruídas | GET | `/api/mqtt/calls` |
| Chamadas em CSV | GET | `/api/mqtt/calls/export` |
| Resumo diário por ramal | GET | `/api/mqtt/calls/daily` |

---

## 16. Backup e restauração — `/system/backup`

**Propósito.** Duas perguntas diferentes na mesma tela: *"como levo esta
configuração para outro sistema?"* e *"como recupero esta instalação?"*. A
primeira se resolve com um arquivo de configuração portável; a segunda, com uma
cópia do banco inteiro. Menu **Backup**, abaixo de Atualizações. Só admin.

**Layout.**

1. **Banner de restauração agendada** (só quando existe) — fica no topo porque
   muda o sentido de tudo abaixo: diz de qual arquivo o banco será substituído,
   com as contagens do arquivo, e que a troca acontece **na próxima
   inicialização**. Botão *Cancelar restauração*.
2. **Exportar configuração** — as quatro seções em caixas de seleção
   (configurações do sistema, ambientes, usuários, devices), campo de
   passphrase e o botão que baixa o `.mwrbak`. O texto avisa que o arquivo
   carrega senhas e que sem a passphrase não há como abri-lo.
3. **Importar configuração** — arquivo + passphrase → *Analisar arquivo*.
   A análise **compara com o que está no banco** e devolve, por grupo
   (configurações, servidores USCall, brokers MQTT, ambientes, usuários,
   devices): quantos itens estão iguais (ignorados), quantos são novos, quantos
   estão em conflito e quantos existem só no sistema. Cada conflito é uma linha
   expansível com uma tabela *campo · no sistema · no arquivo* e o par de
   botões **Manter atual** / **Usar do arquivo**; grupos com mais de um
   conflito ganham a escolha em massa (*todos: manter* / *todos: do arquivo*).
   Só então aparecem as seções a restaurar, o modo e o botão de aplicar.
4. **Backup automático do banco** — liga/desliga, horário (no relógio do
   servidor, com o fuso ao lado), cópias a manter, espaço máximo em MB,
   passphrase do pacote automático (com botão próprio para remover) e o
   resultado da última execução.
5. **Arquivos na pasta de backups** — nome, tipo (*banco completo*,
   *configuração*, *banco anterior*), tamanho, data e as ações baixar /
   restaurar / apagar. No cabeçalho, o seletor para restaurar de um arquivo do
   computador do operador e o caminho da pasta.

**Regras de exibição.**

- Exportar sem passphrase ou sem nenhuma seção marcada é barrado na tela; a API
  também recusa.
- *Restaurar* não aparece em arquivo de configuração (`.mwrbak`) — esse entra
  pelo fluxo de importação, que é outro caminho e outro risco.
- O modo **Substituir** descreve em vermelho o que apaga, e a confirmação diz
  **quantos** itens de cada grupo serão removidos. **Mesclar** é o padrão.
- Valor de segredo (token, senha de broker, hash de senha) **nunca** aparece na
  comparação: o campo mostra `••••` dos dois lados e só informa que difere.
- O lado marcado por padrão é o do arquivo — menos em **Usuários**, onde é o
  atual: restaurar não pode trocar a senha de quem está operando sem que a
  pessoa mande.
- Grupo com muitos conflitos lista os primeiros 200 e avisa: os demais seguem a
  escolha em massa do grupo.
- Depois de aplicar, a tela recompara sozinha — o que aparece é o estado depois
  da restauração, não o de antes.
- Restaurar banco e apagar arquivo pedem confirmação com o nome do arquivo no
  texto.
- Um backup gerado por versão mais nova do middleware é recusado na validação,
  com a mensagem dizendo para atualizar antes — não é oferecido "tentar assim
  mesmo".

### 16.1 Endpoints API consumidos

| Recurso | Método | Path |
|---|---|---|
| Exportar configuração | POST | `/api/backup/export` |
| Comparar pacote com o sistema | POST | `/api/backup/diff` |
| Resumir pacote (sem comparar) | POST | `/api/backup/inspect` |
| Importar configuração | POST | `/api/backup/import` |
| Gerar snapshot | POST | `/api/backup/snapshot` |
| Listar arquivos | GET | `/api/backup/files` |
| Baixar arquivo | GET | `/api/backup/files/{name}` |
| Apagar arquivo | DELETE | `/api/backup/files/{name}` |
| Agendar restauração | POST | `/api/backup/restore` |
| Restaurar de upload | POST | `/api/backup/restore/upload` |
| Restauração pendente | GET | `/api/backup/restore` |
| Cancelar restauração | DELETE | `/api/backup/restore` |
| Config do backup automático | GET/PUT | `/api/backup/settings` |
