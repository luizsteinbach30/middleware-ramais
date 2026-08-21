// Painel ao vivo dos ramais, alimentado pelo coletor MQTT.
//
// A tela responde "o que está acontecendo agora": quem está falando, quem está
// tocando, quem caiu. O dado vem da memória do coletor (`/api/mqtt/live`), que
// é o mesmo instante em que a mensagem chegou do broker — a coleta REST, com
// ciclo de no mínimo 60 s, nunca conseguiria mostrar isso.
//
// Junto vai a saúde da ingestão: uma grade toda verde não vale nada se o
// coletor estiver desconectado há dez minutos, e é o painel que precisa dizer.

import { api } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { fmtTime, fmtTs } from '/static/js/util/datetime.js';

const POLL_MS = 2500;

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// Cores por estado. "Ocupado" e "Tocando" ficam quentes porque são o que puxa o
// olho em um painel de operação; "Indisponivel" é o único vermelho — é o único
// que significa problema (ramal sem registro no PBX).
const ESTADOS = [
  { id: 'disponivel',   rotulo: 'Disponível',   anel: 'ring-green-500/30',  texto: 'text-green-300',  fundo: 'bg-green-500/10',  ponto: 'bg-green-400' },
  { id: 'tocando',      rotulo: 'Tocando',      anel: 'ring-amber-500/30',  texto: 'text-amber-300',  fundo: 'bg-amber-500/10',  ponto: 'bg-amber-400' },
  { id: 'discando',     rotulo: 'Discando',     anel: 'ring-sky-500/30',    texto: 'text-sky-300',    fundo: 'bg-sky-500/10',    ponto: 'bg-sky-400' },
  { id: 'ocupado',      rotulo: 'Em conversa',  anel: 'ring-blue-500/30',   texto: 'text-blue-300',   fundo: 'bg-blue-500/10',   ponto: 'bg-blue-400' },
  { id: 'indisponivel', rotulo: 'Indisponível', anel: 'ring-red-500/30',    texto: 'text-red-300',    fundo: 'bg-red-500/10',    ponto: 'bg-red-400' },
  { id: 'desconhecido', rotulo: 'Não reconhecido', anel: 'ring-gray-600',   texto: 'text-gray-400',   fundo: 'bg-gray-700/30',   ponto: 'bg-gray-500' },
];
const PORID = Object.fromEntries(ESTADOS.map((e) => [e.id, e]));
const estilo = (id) => PORID[id] || PORID.desconhecido;

const state = { filtro: '', busca: '', pausado: false };
let timer = null;
let ultimo = null;   // último payload recebido, para redesenhar sem novo fetch

// ── formatação ──────────────────────────────────────────────────────────────

function duracao(seg) {
  const s = Math.max(0, Math.floor(seg || 0));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}min ${s % 60}s`;
  const h = Math.floor(s / 3600);
  return `${h}h ${Math.floor((s % 3600) / 60)}min`;
}

// ── desenho ─────────────────────────────────────────────────────────────────

function pintarContadores(counts, total) {
  el('ml-counters').innerHTML = [
    { id: '', rotulo: 'Todos', n: total, est: { anel: 'ring-gray-600', texto: 'text-gray-200', fundo: 'bg-gray-700/30', ponto: 'bg-gray-400' } },
    ...ESTADOS.map((e) => ({ id: e.id, rotulo: e.rotulo, n: counts[e.id] || 0, est: e })),
  ]
    // O "não reconhecido" só aparece quando existe: um card zerado permanente
    // vira ruído e ninguém mais repara quando ele deixa de ser zero.
    .filter((c) => c.id !== 'desconhecido' || c.n > 0)
    .map((c) => {
      const ativo = state.filtro === c.id;
      return `<button data-estado="${c.id}" class="text-left rounded-xl px-3.5 py-3 ring-1 ring-inset transition-colors ${c.est.fundo} ${ativo ? 'ring-blue-400' : c.est.anel} hover:ring-blue-400/60">
        <div class="flex items-center gap-1.5">
          <span class="w-1.5 h-1.5 rounded-full ${c.est.ponto}"></span>
          <span class="text-[11px] uppercase tracking-wider ${c.est.texto}">${esc(c.rotulo)}</span>
        </div>
        <div class="text-2xl font-semibold text-gray-100 mt-1 tabular-nums">${c.n}</div>
      </button>`;
    })
    .join('');
}

function pintarSaude(d) {
  const semConexao = !d.running || !d.configured;
  const itens = [
    ['Mensagens/min', d.per_minute ?? 0, ''],
    ['Ramais acompanhados', d.extensions.length, ''],
    ['Fila de gravação', d.queue_depth ?? 0, (d.queue_depth || 0) > 1000 ? 'text-amber-300' : ''],
    // Descarte é a única métrica aqui que significa prova perdida: destaque
    // permanente enquanto for maior que zero.
    ['Descartadas', d.dropped ?? 0, (d.dropped || 0) > 0 ? 'text-red-300' : ''],
    ['Atraso do PBX', d.avg_lag_seconds == null ? '—' : `${d.avg_lag_seconds}s`, ''],
    ['Última mensagem', d.last_message_at ? fmtTime(d.last_message_at) : '—', ''],
  ];
  el('ml-health').innerHTML = itens
    .map(([rot, val, cor]) => `<div>
      <div class="text-gray-500 uppercase tracking-wider text-[10px]">${esc(rot)}</div>
      <div class="text-sm font-medium tabular-nums mt-0.5 ${cor || 'text-gray-200'}">${esc(val)}</div>
    </div>`)
    .join('');
  if (semConexao) {
    el('ml-health').insertAdjacentHTML('afterbegin',
      `<div class="col-span-full rounded-lg px-3 py-2 bg-amber-500/10 ring-1 ring-inset ring-amber-500/30 text-amber-200">
         ${d.configured ? 'O coletor não está rodando — a grade abaixo mostra o último estado conhecido, não o de agora.'
                        : 'Nenhum broker configurado. <a class="underline" href="/config">Configurar o coletor</a>.'}
       </div>`);
  }
}

function visiveis(d) {
  const busca = state.busca.toLowerCase();
  return d.extensions.filter((e) => {
    if (state.filtro && e.status !== state.filtro) return false;
    if (!busca) return true;
    return String(e.ramal).toLowerCase().includes(busca)
        || String(e.numero || '').toLowerCase().includes(busca);
  });
}

function pintarGrade(d) {
  const itens = visiveis(d);
  el('ml-grid-count').textContent = itens.length === d.extensions.length
    ? `${itens.length} ramais`
    : `${itens.length} de ${d.extensions.length}`;

  if (!itens.length) {
    el('ml-grid').innerHTML = `<div class="col-span-full py-10 text-center text-sm text-gray-500">${
      d.extensions.length ? 'Nenhum ramal com esse filtro.'
                          : 'Nenhum ramal recebido ainda. Assim que o broker publicar, eles aparecem aqui.'
    }</div>`;
    return;
  }

  el('ml-grid').innerHTML = itens.map((e) => {
    const est = estilo(e.status);
    // Silêncio NÃO é defeito. Medido contra o broker do cliente: em 10 min só
    // 68 de 243 ramais publicaram alguma coisa — este publicador só fala
    // quando o estado muda. Um alerta de "sem mensagem há X" apagaria 90% da
    // grade o tempo todo, e um painel que grita sempre não avisa nada. Quem
    // responde "o coletor parou?" é a faixa de saúde, que é global.
    const rede = e.device_id
      ? (e.network_status === 'online' ? '' : `<span class="text-red-300/80">rede ${esc(e.network_status)}</span>`)
      : '<span class="text-gray-500">sem device</span>';
    const visto = e.last_seen_at
      ? `última mensagem deste ramal em ${fmtTs(e.last_seen_at)}`
      : 'sem mensagem registrada';
    return `<div class="rounded-lg px-3 py-2.5 ring-1 ring-inset ${est.fundo} ${est.anel}" title="${esc(visto)}">
      <div class="flex items-baseline gap-2">
        <a href="/mqtt-messages?ramal=${encodeURIComponent(e.ramal)}" class="text-base font-semibold text-gray-100 font-mono hover:text-blue-300">${esc(e.ramal)}</a>
        <span class="ml-auto text-[10px] uppercase tracking-wider ${est.texto}">${esc(est.rotulo)}</span>
      </div>
      <div class="mt-1 text-xs text-gray-400 font-mono truncate h-4">${e.numero ? esc(e.numero) : ''}</div>
      <div class="mt-1.5 flex items-center gap-2 text-[10px] text-gray-500">
        <span class="tabular-nums">há ${duracao(e.seconds_in_status)}</span>
        ${rede}
      </div>
    </div>`;
  }).join('');
}

function pintarFita(d) {
  if (!d.transitions.length) {
    el('ml-fita').innerHTML = `<div class="px-4 py-10 text-center text-sm text-gray-500">Nenhuma transição registrada ainda.</div>`;
    return;
  }
  el('ml-fita').innerHTML = d.transitions.map((t) => {
    const est = estilo(t.status);
    return `<div class="px-4 py-2 flex items-center gap-3 text-xs">
      <span class="w-1.5 h-1.5 rounded-full ${est.ponto} flex-shrink-0"></span>
      <span class="font-mono text-gray-200 w-14 flex-shrink-0">${esc(t.ramal)}</span>
      <span class="${est.texto} w-24 flex-shrink-0">${esc(est.rotulo)}</span>
      <span class="font-mono text-gray-500 flex-1 truncate">${esc(t.numero || '')}</span>
      <span class="text-gray-500 tabular-nums flex-shrink-0" title="${esc(fmtTs(t.received_at))}">${esc(fmtTime(t.received_at))}</span>
    </div>`;
  }).join('');
}

function pintarColetor(d) {
  const pill = el('ml-collector');
  if (!d.configured) {
    pill.className = 'px-2.5 py-1 rounded-full text-[11px] font-medium bg-gray-600/30 text-gray-400 ring-1 ring-inset ring-gray-600 whitespace-nowrap';
    pill.innerHTML = 'coletor: não configurado — <a class="underline" href="/config">configurar</a>';
    return;
  }
  const ligado = d.running && (d.per_minute > 0 || d.last_message_at);
  pill.className = `px-2.5 py-1 rounded-full text-[11px] font-medium ring-1 ring-inset whitespace-nowrap ${
    ligado ? 'bg-green-500/15 text-green-300 ring-green-500/30' : 'bg-red-500/15 text-red-300 ring-red-500/30'}`;
  pill.textContent = ligado ? `coletando · ${d.per_minute} msg/min` : 'coletor sem tráfego';
}

function redesenhar() {
  if (!ultimo) return;
  const total = ultimo.extensions.length;
  pintarContadores(ultimo.counts, total);
  pintarSaude(ultimo);
  pintarGrade(ultimo);
  pintarFita(ultimo);
  pintarColetor(ultimo);
  injectIcons();
}

// ── ciclo ───────────────────────────────────────────────────────────────────

async function puxar() {
  try {
    ultimo = await api('/api/mqtt/live');
    redesenhar();
  } catch (e) {
    el('ml-grid').innerHTML = `<div class="col-span-full py-10 text-center text-sm text-red-300">Falha ao ler o estado: ${esc(e.message || e)}</div>`;
  }
}

function pausar(on) {
  state.pausado = on;
  el('ml-pause-dot').className = `w-1.5 h-1.5 rounded-full ${on ? 'bg-gray-500' : 'bg-green-400 animate-pulse'}`;
  el('ml-pause-label').textContent = on ? 'Retomar' : 'Pausar';
  clearInterval(timer);
  if (!on) {
    timer = setInterval(puxar, POLL_MS);
    puxar();
  }
}

if (el('ml-grid')) {
  el('ml-counters').addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-estado]');
    if (!btn) return;
    // Clicar no card já marcado desliga o filtro — evita ficar preso em um
    // estado vazio sem entender por que a grade sumiu.
    state.filtro = state.filtro === btn.dataset.estado ? '' : btn.dataset.estado;
    redesenhar();
  });
  el('ml-busca').addEventListener('input', () => {
    state.busca = el('ml-busca').value.trim();
    redesenhar();
  });
  el('ml-pause').addEventListener('click', () => pausar(!state.pausado));

  // Aba escondida não precisa de tráfego: o navegador segura os timers, mas o
  // fetch imediato ao voltar evita a tela mostrar dado velho por 2,5 s.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !state.pausado) puxar();
  });

  pausar(false);
}
