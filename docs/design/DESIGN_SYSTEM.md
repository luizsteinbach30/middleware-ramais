# Design System — Middleware USCall Monitor v2.0

**Referência viva:** [mw-screens-reference.jsx](mw-screens-reference.jsx) — 1399 linhas com **todas as 10 telas** implementadas em React/Tailwind. Esta é a fonte da verdade visual; este documento extrai os tokens e padrões para implementação no Jinja2 do projeto.

## Paleta (Tailwind tokens)

| Função | Token | Uso |
|---|---|---|
| Fundo app | `bg-gray-900` | corpo |
| Fundo sidebar/headers | `bg-gray-950` | sidebar, header sticky |
| Card | `bg-gray-800` | todo card de conteúdo |
| Card alternativo | `bg-gray-900/40` ou `bg-gray-900/60` | nested cards e subáreas |
| Texto primário | `text-gray-100` | títulos, valores |
| Texto secundário | `text-gray-200` | corpo |
| Texto descritivo | `text-gray-400` | labels |
| Texto fraco | `text-gray-500` | hints |
| Ring/borda | `ring-1 ring-gray-700` | bordas de cards e inputs |
| Divisor | `divide-gray-800` ou `border-gray-800` | tabelas |
| Acento (info/ações) | `bg-blue-500` / `text-blue-400` / `text-blue-300` | botão primário, links, KPIs |
| Sucesso | `text-green-400` / `bg-green-500/15` | online, ok |
| Erro | `text-red-400` / `bg-red-500/15` | offline, falha |
| Atenção | `text-yellow-400` / `bg-yellow-500/15` | indisponível, warn |
| Info secundária | `text-indigo-300` / `bg-indigo-500/15` | latência, results |

**Padrão de chip/badge translúcido:** `bg-{color}-500/15 text-{color}-400 ring-1 ring-inset ring-{color}-500/30`.

## Tipografia

- Fonte: stack default Tailwind (`-apple-system, ...`).
- `antialiased` no body.
- Títulos h1: `text-lg font-semibold text-gray-100`.
- Títulos card: `text-sm font-semibold text-gray-100`.
- Labels (uppercase tag): `text-[11px] uppercase tracking-wider font-semibold text-gray-500` ou `text-gray-400`.
- KPIs grandes: `text-2xl font-bold` ou `text-3xl font-bold`.
- Mono: `font-mono text-xs` para timestamps, IPs, MACs, hashes.
- Numérico em tabelas: `tabular-nums`.

## Layout

- Sidebar: `w-64 shrink-0 bg-gray-950 border-r border-gray-800 h-screen sticky top-0`.
- Conteúdo principal: `flex-1 min-w-0`.
- Header sticky: `border-b border-gray-800 bg-gray-900/80 backdrop-blur sticky top-0 z-20`.
- Container de página: `px-6 py-6 space-y-6` (ou `space-y-4`/`space-y-5` quando mais denso).
- Grids:
  - KPI dashboard: `grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3`.
  - Forms: `grid md:grid-cols-2 gap-4` ou `md:grid-cols-3 gap-4`.
  - Split (lista + viewer): `grid grid-cols-1 lg:grid-cols-2 gap-6`.
- Cards: `rounded-xl ring-1 ring-gray-700 bg-gray-800 p-5` (ou `p-4`/`p-0` quando tabela embutida).

## Componentes

### Badge
```html
<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-{color}-500/15 text-{color}-400 ring-{color}-500/30">
  <span class="w-1.5 h-1.5 rounded-full bg-{color}-400"></span>
  texto
</span>
```
Tons: `green`, `red`, `blue`, `yellow`, `gray`, `indigo`. `dot` é opcional.

### Botão
- Primary: `bg-blue-500 hover:bg-blue-400 text-white shadow-sm`.
- Danger: `bg-red-500 hover:bg-red-400 text-white`.
- Success: `bg-green-500 hover:bg-green-400 text-white`.
- Ghost: `bg-transparent hover:bg-gray-700/60 text-gray-200 ring-1 ring-inset ring-gray-700`.
- Subtle: `bg-gray-800 hover:bg-gray-700 text-gray-200 ring-1 ring-inset ring-gray-700`.
- Tamanhos: `px-2.5 py-1.5 text-xs` (sm), `px-3.5 py-2 text-sm` (md), `px-4 py-2.5 text-sm` (lg).
- Estado disabled: `disabled:opacity-50 disabled:cursor-not-allowed`.
- Wrapper: `inline-flex items-center gap-2 rounded-lg font-medium transition-colors`.

### Input
```html
<input class="bg-gray-900 ring-1 ring-inset ring-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"/>
```

### Select
Mesma classe do Input.

### Toggle (switch)
- Wrapper: `inline-flex items-center gap-3 cursor-pointer select-none`.
- Track: `relative inline-block w-11 h-6` com `<input class="sr-only peer">` + duas spans:
  - Trilho: `absolute inset-0 rounded-full bg-gray-700 peer-checked:bg-blue-500 transition-colors`.
  - Botão: `absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform peer-checked:translate-x-5`.

### Field
- `<label class="flex flex-col gap-1.5">`.
- Span de label: `text-xs font-semibold text-gray-300` com hint opcional `font-normal text-gray-500`.
- Erro: `text-xs text-red-400 flex items-center gap-1` com ícone alert.

### MaskedInput (token)
- Estado bloqueado: input desabilitado com valor `••••••••••••` + botão "Alterar" (ghost/subtle, ícone `edit`).
- Ao clicar Alterar: input habilitado para colar novo valor, botão "Cancelar".
- **Nunca renderizar o valor real do token.**

### Card
`bg-gray-800 ring-1 ring-gray-700 rounded-xl p-5`.

### Tabelas
- Wrapper: `Card pad="p-0"` com `<div class="overflow-x-auto">`.
- `<thead>`: `bg-gray-900/60 text-gray-400 text-xs uppercase tracking-wider`.
- `<th>`: `text-left px-4 py-3 font-semibold` (ou `text-right` para colunas numéricas/ações).
- `<tbody>`: `divide-y divide-gray-800`.
- `<tr>` hover: `hover:bg-gray-700/30 transition`.
- `<td>`: `px-4 py-2.5`.
- Linhas de erro: `bg-red-500/5`. Linhas de teste: `bg-indigo-500/5`.

### Modais
- Overlay: `fixed inset-0 bg-black/60 z-50 grid place-items-center px-4`.
- Painel: `bg-gray-800 ring-1 ring-gray-700 rounded-xl max-w-2xl w-full`.
- Header do modal: `px-5 py-4 border-b border-gray-700 flex items-center justify-between`.
- Conteúdo `<pre>`: `bg-gray-950 px-5 py-4 text-xs font-mono text-gray-300 max-h-96 overflow-auto rounded-b-xl`.

### Banner global
```html
<div class="px-6 py-2 bg-yellow-500/10 border-b border-yellow-500/30 text-xs text-yellow-200 flex items-center gap-2">
  <icon-alert/> Serviço degradado — coleta pausada.
</div>
```

### Sticky save bar
```html
<div class="px-6 py-2 bg-yellow-500/10 border-b border-yellow-500/30 text-xs text-yellow-300">
  Você tem alterações não salvas.
</div>
```

### Charts (SVG)
- Cores das linhas:
  - Online: `#34d399` (verde-400).
  - Offline: `#f87171` (vermelho-400).
  - Latência: `#60a5fa` (azul-400) com gradient `0%→35%→0%`.
- Grid lines: `rgba(148,163,184,0.12)` com `stroke-dasharray="3 4"`.
- Pontos de queda: `r="3" fill="#f87171"`.

## Iconografia

Ícones inline SVG (estilo Feather/Lucide, `stroke-width: 2`, `stroke-linecap: round`, `viewBox 0 0 24 24`). Lista usada:

```
home phone database webhook list settings download refresh
eye eye-off check x alert arrow-left play log-out package
user activity shield menu chevron-r copy edit play-circle
```

A implementação no projeto Jinja2 deve replicar como **partial** (`templates/partials/icon.html`) com `{% include %}` ou um helper Jinja `{{ icon('home', size=16) }}`. Os paths SVG estão em [mw-screens-reference.jsx:134-163](mw-screens-reference.jsx) — copiar literalmente.

## Telas (resumo do que cada uma renderiza)

1. **Login** — card central 400px, gradiente sutil de fundo, logo redondo `bg-blue-500/15`, campos com revealer de senha, mensagem de erro em chip vermelho.
2. **Dashboard** — 6 KPIs em grid 6, gráfico online/offline 24h em coluna 2/3, cards "Webhooks 24h" e "Sistema" na coluna 1/3.
3. **Devices list** — filtros em card, badges resumo, tabela com 9 colunas (Ramal, IP, MAC, Modelo, USCall, Rede, Lat., Last seen, Ações), paginação no rodapé.
4. **Device detail** — 4 KPIs (status lógico, rede, latência, MAC), gráfico de latência com seletor 24h/7d/30d/custom, tabela de pings recentes.
5. **Collections** — split list + viewer JSON, lista com `bg-blue-500/10 ring-blue-500/30` para selecionado, viewer com syntax highlight pré-rendered.
6. **Webhook logs** — filtros, tabela com badge de tipo, badge de HTTP por faixa (`>=500` red, `>=400` yellow, `=0` gray, ok green), modal de payload.
7. **System logs** — filtros + toggle de auto-refresh, tabela com badge de nível, chip de módulo, modal de contexto.
8. **Config** — múltiplos cards de seção (cliente, USCall, intervalos, monitoramento, webhooks, retenção), MaskedInput em todos tokens, sticky banner amarelo quando dirty, botão "Testar" para USCall e cada webhook.
9. **Updates** — card principal split (versão atual + nova versão disponível em ring azul), barra de progresso animada durante update, tabela de histórico com badge de status (success/rolled_back/falha).
10. **Account** — split: "Trocar senha" com checklist de política à medida que digita, "Sessão atual" + "Política de senha" lado a lado.
11. **404** — card centralizado com ícone alert e CTA "Voltar ao dashboard".

## Banner sticky de serviço degradado

Quando `/api/system/readyz` retorna 503, exibir no topo do conteúdo (fora do header):
```
bg-yellow-500/10 border-b border-yellow-500/30 text-yellow-200
```
Com link clicável para `/logs`.

## Adaptação para Jinja2 + JS vanilla

Como o projeto não usa React, o `noc-frontend` deve:
1. **Replicar tokens 1:1** — mesmas classes Tailwind por componente.
2. Criar partials (`base.html`, `partials/sidebar.html`, `partials/header.html`, `partials/icon.html`, `partials/badge.html`, `partials/empty_state.html`, `partials/pagination.html`).
3. Criar helpers Jinja (`{{ badge('green', 'online', dot=True) }}`).
4. Modais como divs `hidden` controladas por JS module em `static/js/components/modal.js`.
5. Charts via Chart.js com paleta exata do design.
6. Estados interativos (filtros, modal, toggle de auto-refresh) em JS vanilla por página em `static/js/pages/<screen>.js`.

## Critérios de fidelidade

- [ ] Cada tela renderiza com pixel-parity razoável da referência (paddings, raios, fontes, cores).
- [ ] Hover, focus, disabled em botões idênticos ao design.
- [ ] Badges com `ring-1 ring-inset` (não bordas sólidas).
- [ ] Tokens jamais aparecem em texto plano no DOM.
- [ ] Todos os ícones via SVG inline (sem dependência externa de Font Awesome ou similar).
