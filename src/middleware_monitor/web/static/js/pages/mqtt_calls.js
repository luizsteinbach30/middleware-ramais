// Tela de chamadas reconstruídas.
//
// O PBX não publica chamadas — publica o estado de cada ramal. O que se vê aqui
// é o que foi deduzido dessas transições, e a tabela mostra **uma ponta por
// linha**: uma ligação entre dois ramais aparece duas vezes. É proposital, e a
// nota acima da tabela existe para ninguém achar que o número está dobrado.

import { api, qs } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { fmtTs } from '/static/js/util/datetime.js';

const PAGE = 100;

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const RESULTADO = {
  atendida:     ['green',  'atendida'],
  perdida:      ['red',    'perdida'],
  // Quem discou e não foi atendido não é o mesmo problema de quem recebeu e não
  // atendeu — a cor separa os dois para o olho não somar.
  nao_atendida: ['amber',  'não atenderam'],
  em_curso:     ['blue',   'em curso'],
  indeterminada:['gray',   'indeterminada'],
};
const TOM = {
  green: 'bg-green-500/15 text-green-300 ring-green-500/30',
  red:   'bg-red-500/15 text-red-300 ring-red-500/30',
  amber: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  blue:  'bg-blue-500/15 text-blue-300 ring-blue-500/30',
  gray:  'bg-gray-500/15 text-gray-400 ring-gray-500/30',
};

const state = { last: '24h', ramal: '', numero: '', direcao: '', outcome: '', offset: 0 };
let total = 0;

function dur(seg) {
  if (seg === null || seg === undefined) return '—';
  const s = Math.max(0, Math.floor(seg));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}min ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}min`;
}

function selo(outcome) {
  const [tom, rotulo] = RESULTADO[outcome] || RESULTADO.indeterminada;
  return `<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${TOM[tom]}">${rotulo}</span>`;
}

function direcao(d) {
  if (d === 'entrante') return '<span class="text-gray-300">recebida</span>';
  if (d === 'sainte') return '<span class="text-gray-300">feita</span>';
  return '<span class="text-gray-500">—</span>';
}

function linha(c) {
  return `<tr class="hover:bg-gray-700/30">
    <td class="px-4 py-2 text-gray-300 font-mono text-xs">${esc(fmtTs(c.started_at))}</td>
    <td class="px-4 py-2">
      <a href="/mqtt-messages?ramal=${encodeURIComponent(c.ramal)}" class="font-mono text-gray-100 hover:text-blue-300">${esc(c.ramal)}</a>
    </td>
    <td class="px-4 py-2 text-xs">${direcao(c.direcao)}</td>
    <td class="px-4 py-2 font-mono text-gray-300 text-xs">${c.numero ? esc(c.numero) : '<span class="text-gray-600">—</span>'}</td>
    <td class="px-4 py-2">${selo(c.outcome)}</td>
    <td class="px-4 py-2 text-right tabular-nums text-gray-400 text-xs">${dur(c.ring_seconds)}</td>
    <td class="px-4 py-2 text-right tabular-nums text-gray-200 text-xs">${dur(c.talk_seconds)}</td>
  </tr>`;
}

function filtros() {
  return {
    last: state.last,
    ramal: state.ramal || undefined,
    numero: state.numero || undefined,
    direcao: state.direcao || undefined,
    outcome: state.outcome || undefined,
  };
}

async function buscar({ acrescentar = false } = {}) {
  const tbody = el('mc-tbody');
  if (!acrescentar) {
    state.offset = 0;
    tbody.innerHTML = `<tr><td colspan="7" class="px-4 py-10 text-center text-sm text-gray-500">Buscando…</td></tr>`;
  }
  try {
    const d = await api('/api/mqtt/calls' + qs({ ...filtros(), limit: PAGE, offset: state.offset }));
    total = d.total;
    const html = d.items.map(linha).join('');
    if (acrescentar) tbody.insertAdjacentHTML('beforeend', html);
    else tbody.innerHTML = html || `<tr><td colspan="7" class="px-4 py-10 text-center text-sm text-gray-500">Nenhuma chamada no período.</td></tr>`;
    state.offset += d.items.length;
    el('mc-count').textContent = total
      ? `${state.offset} de ${total} pontas de chamada`
      : 'nenhuma chamada';
    el('mc-more').classList.toggle('hidden', state.offset >= total);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="px-4 py-10 text-center text-sm text-red-300">Falha na busca: ${esc(e.message || e)}</td></tr>`;
  }
  injectIcons();
}

function marcarPreset() {
  document.querySelectorAll('#mc-presets [data-last]').forEach((b) => {
    const ativo = b.dataset.last === state.last;
    b.classList.toggle('bg-blue-500/15', ativo);
    b.classList.toggle('text-blue-300', ativo);
  });
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
const buscarComAtraso = debounce(() => buscar(), 250);

if (el('mc-tbody')) {
  document.querySelectorAll('#mc-presets [data-last]').forEach((b) => b.addEventListener('click', () => {
    state.last = b.dataset.last;
    marcarPreset();
    buscar();
  }));
  for (const [id, campo] of [['mc-ramal', 'ramal'], ['mc-numero', 'numero']]) {
    el(id).addEventListener('input', () => { state[campo] = el(id).value.trim(); buscarComAtraso(); });
  }
  for (const [id, campo] of [['mc-direcao', 'direcao'], ['mc-outcome', 'outcome']]) {
    el(id).addEventListener('change', () => { state[campo] = el(id).value; buscar(); });
  }
  el('mc-refresh').addEventListener('click', () => buscar());
  el('mc-more').addEventListener('click', () => buscar({ acrescentar: true }));

  marcarPreset();
  buscar();
}
