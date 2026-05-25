import { api, qs } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const state = {
  search: '', environment: 'all', network: 'all', logical: 'all',
  ip_from: '', ip_to: '', ramal_from: '', ramal_to: '',
  page: 1, size: 50,
};

// Seleção persiste entre os re-renders do auto-refresh.
const selected = new Set();
// Cache dos ambientes (para o filtro e os modais).
let environments = [];

function syncFromUrl() {
  const p = new URLSearchParams(location.search);
  state.search = p.get('search') || '';
  state.environment = p.get('environment') || 'all';
  state.network = p.get('network') || 'all';
  state.logical = p.get('logical') || 'all';
  state.ip_from = p.get('ip_from') || '';
  state.ip_to = p.get('ip_to') || '';
  state.ramal_from = p.get('ramal_from') || '';
  state.ramal_to = p.get('ramal_to') || '';
  state.page = parseInt(p.get('page') || '1', 10);
}

function syncToUrl() {
  const p = new URLSearchParams();
  if (state.search) p.set('search', state.search);
  if (state.environment !== 'all') p.set('environment', state.environment);
  if (state.network !== 'all') p.set('network', state.network);
  if (state.logical !== 'all') p.set('logical', state.logical);
  for (const k of ['ip_from', 'ip_to', 'ramal_from', 'ramal_to']) {
    if (state[k]) p.set(k, state[k]);
  }
  if (state.page > 1) p.set('page', String(state.page));
  history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p : ''));
}

function badge(tone, text, dot = false) {
  const tones = {
    green: 'bg-green-500/15 text-green-400 ring-green-500/30',
    red: 'bg-red-500/15 text-red-400 ring-red-500/30',
    blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30',
    yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30',
    gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30',
  };
  const dots = { green: 'bg-green-400', red: 'bg-red-400', blue: 'bg-blue-400', yellow: 'bg-yellow-400', gray: 'bg-gray-400' };
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${tones[tone]}">${dot ? `<span class="w-1.5 h-1.5 rounded-full ${dots[tone]}"></span>` : ''}${text}</span>`;
}

function logicalBadge(s) {
  if (s === 'available') return badge('blue', 'disponível', true);
  if (s === 'unavailable') return badge('yellow', 'indisponível', true);
  return badge('gray', '—', true);
}
function networkBadge(s) {
  if (s === 'online') return badge('green', 'online', true);
  if (s === 'offline') return badge('red', 'offline', true);
  return badge('gray', '—', true);
}

function updateBulkBar() {
  const bar = document.getElementById('bulk-bar');
  const n = selected.size;
  bar.classList.toggle('hidden', n === 0);
  bar.classList.toggle('flex', n > 0);
  document.getElementById('bulk-count').textContent =
    `${n} ${n === 1 ? 'selecionado' : 'selecionados'}`;
}

function refreshSelectAll(pageIds) {
  const el = document.getElementById('select-all');
  if (!pageIds.length) { el.checked = false; el.indeterminate = false; return; }
  const sel = pageIds.filter((id) => selected.has(id)).length;
  el.checked = sel === pageIds.length;
  el.indeterminate = sel > 0 && sel < pageIds.length;
}

async function load() {
  syncToUrl();
  try {
    const [data, summary] = await Promise.all([
      api('/api/devices' + qs(state)),
      api('/api/devices/summary'),
    ]);
    document.getElementById('dev-count').textContent = `${data.total} ramais (página ${data.page})`;
    document.getElementById('summary-badges').innerHTML = [
      badge('blue', `USCall ${summary.logical_available} disponíveis`, true),
      badge('yellow', `${summary.logical_unavailable} indisponíveis`, true),
      badge('green', `Rede ${summary.network_online} online`, true),
      badge('red', `${summary.network_offline} offline`, true),
    ].join('');

    const pageIds = data.items.map((d) => d.id);
    const tbody = document.getElementById('devices-tbody');
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="11" class="px-4 py-12 text-center text-sm text-gray-500">Nenhum device com esses filtros.</td></tr>`;
    } else {
      tbody.innerHTML = data.items.map((d) => {
        const linkCell = d.extension_environment_id
          ? `<a href="/extension-configurator/environments/${d.extension_environment_id}" class="text-blue-400 hover:underline text-xs" onclick="event.stopPropagation()">${esc(d.extension_environment_nome)}</a>
             <div class="text-[11px] text-gray-500">ramal ${esc(d.extension_line_ramal) || '—'}${d.extension_line_nome_visivel ? ' · ' + esc(d.extension_line_nome_visivel) : ''}</div>`
          : `<span class="text-xs text-gray-500">— sem vínculo</span>`;
        const checked = selected.has(d.id) ? 'checked' : '';
        return `
        <tr class="hover:bg-gray-700/30 transition cursor-pointer" data-id="${d.id}">
          <td class="px-4 py-2.5"><input type="checkbox" data-select="${d.id}" ${checked} class="rounded border-gray-600 bg-gray-900 text-blue-500 focus:ring-blue-500/50 cursor-pointer"/></td>
          <td class="px-4 py-2.5 font-bold text-gray-100">${esc(d.name)}</td>
          <td class="px-4 py-2.5 font-mono text-xs text-gray-300">${esc(d.ip) || '—'}</td>
          <td class="px-4 py-2.5 font-mono text-xs text-gray-500">${esc(d.mac) || '—'}</td>
          <td class="px-4 py-2.5 text-gray-400">${esc(d.model) || '—'}</td>
          <td class="px-4 py-2.5">${linkCell}</td>
          <td class="px-4 py-2.5">${logicalBadge(d.logical_status)}</td>
          <td class="px-4 py-2.5">${networkBadge(d.network_status)}</td>
          <td class="px-4 py-2.5 text-right font-mono tabular-nums text-gray-300">${d.latency_ms != null ? d.latency_ms + ' ms' : '—'}</td>
          <td class="px-4 py-2.5 text-gray-500 text-xs">${fmtTs(d.last_seen_at)}</td>
          <td class="px-4 py-2.5 text-right">
            <div class="inline-flex gap-1">
              <button data-refresh="${d.id}" class="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100" title="Forçar ping"><span data-icon="refresh"></span></button>
              <a href="/devices/${d.id}" class="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100" title="Detalhes"><span data-icon="chevron-r"></span></a>
            </div>
          </td>
        </tr>`;
      }).join('');
      tbody.querySelectorAll('tr').forEach((tr) => {
        tr.addEventListener('click', (e) => {
          if (e.target.closest('button,a,input')) return;
          location.href = '/devices/' + tr.dataset.id;
        });
      });
      tbody.querySelectorAll('[data-select]').forEach((cb) => {
        cb.addEventListener('change', (ev) => {
          ev.stopPropagation();
          const id = parseInt(cb.dataset.select, 10);
          if (cb.checked) selected.add(id); else selected.delete(id);
          updateBulkBar();
          refreshSelectAll(pageIds);
        });
      });
      tbody.querySelectorAll('[data-refresh]').forEach((b) => {
        b.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          b.disabled = true;
          try { await api(`/api/devices/${b.dataset.refresh}/refresh`, { method: 'POST' }); load(); }
          catch (e) { toast.error('Falha ao pingar'); }
          finally { b.disabled = false; }
        });
      });
    }
    document.getElementById('page-info').textContent =
      `Página ${data.page} · ${data.items.length} de ${data.total}`;
    document.getElementById('page-prev').disabled = data.page <= 1;
    document.getElementById('page-next').disabled = data.page * data.size >= data.total;
    refreshSelectAll(pageIds);
    updateBulkBar();
    injectIcons();
    return pageIds;
  } catch (_e) { return []; }
}

// --- ambientes (filtro typeahead + modais) ---
const envInput = document.getElementById('f-environment-input');
const envList = document.getElementById('f-environment-list');
let envItemsCur = [];   // itens renderizados no dropdown atual
let envHi = -1;         // índice destacado (navegação por teclado)
let envQuery = '';      // termo de busca do último render

function labelForEnv(id) {
  if (id === 'all') return '';            // placeholder "Todos"
  if (id === 'none') return 'Sem vínculo';
  const e = environments.find((x) => x.id === id);
  return e ? e.nome : '';
}
function applyEnvLabel() { envInput.value = labelForEnv(state.environment); }

function envCandidates(query) {
  const base = [
    { id: 'all', nome: 'Todos' },
    { id: 'none', nome: 'Sem vínculo' },
    ...environments,
  ];
  const q = (query || '').trim().toLowerCase();
  const filtered = q ? base.filter((e) => e.nome.toLowerCase().includes(q)) : base;
  return filtered.slice(0, 100);
}

function renderEnvList(query) {
  envQuery = query || '';
  envItemsCur = envCandidates(query);
  if (envHi >= envItemsCur.length) envHi = envItemsCur.length - 1;
  envList.innerHTML = envItemsCur.length
    ? envItemsCur.map((e, i) => `
      <li data-id="${esc(e.id)}" data-i="${i}"
          class="px-3 py-1.5 cursor-pointer truncate ${i === envHi ? 'bg-blue-500/20 text-blue-200' : 'text-gray-200 hover:bg-gray-700/60'}">${esc(e.nome)}</li>`).join('')
    : '<li class="px-3 py-1.5 text-gray-500">Nenhum ambiente</li>';
}

function openEnv(query) { renderEnvList(query); envList.classList.remove('hidden'); }
function closeEnv() { envList.classList.add('hidden'); envHi = -1; }

function selectEnv(id) {
  state.environment = id;
  state.page = 1;
  applyEnvLabel();
  closeEnv();
  load();
}

envInput.addEventListener('focus', () => { envHi = -1; openEnv(''); envInput.select(); });
envInput.addEventListener('input', () => { envHi = 0; openEnv(envInput.value); });
envInput.addEventListener('keydown', (e) => {
  if (envList.classList.contains('hidden') && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
    openEnv(''); envHi = 0; renderEnvList(''); return;
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault(); envHi = Math.min(envHi + 1, envItemsCur.length - 1); renderEnvList(envQuery);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault(); envHi = Math.max(envHi - 1, 0); renderEnvList(envQuery);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (envHi >= 0 && envItemsCur[envHi]) selectEnv(envItemsCur[envHi].id);
  } else if (e.key === 'Escape') {
    closeEnv(); applyEnvLabel(); envInput.blur();
  }
});
// mousedown (não click) p/ selecionar antes do blur fechar o dropdown
envList.addEventListener('mousedown', (e) => {
  const li = e.target.closest('li[data-id]');
  if (!li) return;
  e.preventDefault();
  selectEnv(li.dataset.id);
});
envInput.addEventListener('blur', () => {
  setTimeout(() => { closeEnv(); applyEnvLabel(); }, 120);
});

function fillEnvironmentControls() {
  applyEnvLabel();  // reflete o ambiente atual (ex.: veio da URL)
  document.getElementById('modal-add-env').innerHTML = environments.length
    ? environments.map((e) => `<option value="${esc(e.id)}">${esc(e.nome)} · ${esc(e.modelo_telefone)}</option>`).join('')
    : '<option value="" disabled>Nenhum ambiente criado</option>';
}

async function loadEnvironments() {
  try {
    const { environments: envs } = await api('/api/extension-configurator/environments');
    environments = envs || [];
  } catch (_e) { environments = []; }
  fillEnvironmentControls();
}

// --- elementos de filtro ---
const search = document.getElementById('f-search');
const network = document.getElementById('f-network');
const logical = document.getElementById('f-logical');
const ipFrom = document.getElementById('f-ip-from');
const ipTo = document.getElementById('f-ip-to');
const ramalFrom = document.getElementById('f-ramal-from');
const ramalTo = document.getElementById('f-ramal-to');

const debounced = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

syncFromUrl();
search.value = state.search;
network.value = state.network;
logical.value = state.logical;
ipFrom.value = state.ip_from;
ipTo.value = state.ip_to;
ramalFrom.value = state.ramal_from;
ramalTo.value = state.ramal_to;

search.addEventListener('input', debounced((e) => { state.search = e.target.value; state.page = 1; load(); }));
network.addEventListener('change', (e) => { state.network = e.target.value; state.page = 1; load(); });
logical.addEventListener('change', (e) => { state.logical = e.target.value; state.page = 1; load(); });
ipFrom.addEventListener('input', debounced((e) => { state.ip_from = e.target.value.trim(); state.page = 1; load(); }, 400));
ipTo.addEventListener('input', debounced((e) => { state.ip_to = e.target.value.trim(); state.page = 1; load(); }, 400));
ramalFrom.addEventListener('input', debounced((e) => { state.ramal_from = e.target.value.trim(); state.page = 1; load(); }, 400));
ramalTo.addEventListener('input', debounced((e) => { state.ramal_to = e.target.value.trim(); state.page = 1; load(); }, 400));

document.getElementById('f-clear').addEventListener('click', () => {
  Object.assign(state, {
    search: '', environment: 'all', network: 'all', logical: 'all',
    ip_from: '', ip_to: '', ramal_from: '', ramal_to: '', page: 1,
  });
  search.value = ''; envInput.value = ''; network.value = 'all'; logical.value = 'all';
  ipFrom.value = ''; ipTo.value = ''; ramalFrom.value = ''; ramalTo.value = '';
  closeEnv();
  load();
});

document.getElementById('page-prev').addEventListener('click', () => { if (state.page > 1) { state.page--; load(); } });
document.getElementById('page-next').addEventListener('click', () => { state.page++; load(); });

// --- seleção em massa ---
document.getElementById('select-all').addEventListener('change', async (e) => {
  const checked = e.target.checked;
  document.querySelectorAll('[data-select]').forEach((cb) => {
    const id = parseInt(cb.dataset.select, 10);
    cb.checked = checked;
    if (checked) selected.add(id); else selected.delete(id);
  });
  updateBulkBar();
});

document.getElementById('bulk-clear').addEventListener('click', () => {
  selected.clear();
  document.querySelectorAll('[data-select]').forEach((cb) => { cb.checked = false; });
  document.getElementById('select-all').checked = false;
  updateBulkBar();
});

// --- modais (helpers) ---
const show = (id) => document.getElementById(id).classList.remove('hidden');
const hide = (id) => document.getElementById(id).classList.add('hidden');
function selectedIds() { return [...selected]; }
function selCountLabel() {
  const n = selected.size;
  return `${n} device${n === 1 ? '' : 's'} selecionado${n === 1 ? '' : 's'}`;
}
async function afterBulk() {
  selected.clear();
  await Promise.all([load(), loadEnvironments()]);
}

// Adicionar a ambiente
document.getElementById('bulk-add').addEventListener('click', () => {
  if (!selected.size) return;
  if (!environments.length) { toast.error('Nenhum ambiente criado ainda'); return; }
  document.getElementById('modal-add-info').textContent = selCountLabel();
  show('modal-add');
});
document.getElementById('modal-add-cancel').addEventListener('click', () => hide('modal-add'));
document.getElementById('modal-add').addEventListener('click', (e) => { if (e.target.id === 'modal-add') hide('modal-add'); });
document.getElementById('modal-add-confirm').addEventListener('click', async () => {
  const envId = document.getElementById('modal-add-env').value;
  if (!envId) return;
  const btn = document.getElementById('modal-add-confirm');
  btn.disabled = true;
  try {
    const res = await api('/api/devices/bulk/add-to-environment', {
      method: 'POST', body: { environment_id: envId, device_ids: selectedIds() },
    });
    hide('modal-add');
    toast.success(`${res.added} adicionado(s) a "${res.environment_nome}"${res.skipped ? ` · ${res.skipped} pulado(s)` : ''}`);
    await afterBulk();
  } catch (err) {
    toast.error('Erro: ' + err.message);
  } finally { btn.disabled = false; }
});

// Criar ambiente
document.getElementById('bulk-create').addEventListener('click', async () => {
  if (!selected.size) return;
  document.getElementById('modal-create-info').textContent = selCountLabel();
  document.getElementById('modal-create-nome').value = '';
  const sel = document.getElementById('modal-create-modelo');
  if (!sel.options.length) {
    try {
      const { models } = await api('/api/extension-configurator/phone-models');
      sel.innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
    } catch (_e) { /* ignore */ }
  }
  show('modal-create');
  document.getElementById('modal-create-nome').focus();
});
document.getElementById('modal-create-cancel').addEventListener('click', () => hide('modal-create'));
document.getElementById('modal-create').addEventListener('click', (e) => { if (e.target.id === 'modal-create') hide('modal-create'); });
document.getElementById('modal-create-confirm').addEventListener('click', async () => {
  const nome = document.getElementById('modal-create-nome').value.trim();
  const modelo = document.getElementById('modal-create-modelo').value;
  if (!nome) { toast.error('Nome obrigatório'); return; }
  const btn = document.getElementById('modal-create-confirm');
  btn.disabled = true;
  try {
    const res = await api('/api/devices/bulk/create-environment', {
      method: 'POST', body: { nome, modelo_telefone: modelo, device_ids: selectedIds() },
    });
    hide('modal-create');
    toast.success(`Ambiente "${res.environment_nome}" criado · ${res.added} device(s)${res.skipped ? ` · ${res.skipped} pulado(s)` : ''}`);
    await afterBulk();
  } catch (err) {
    toast.error('Erro: ' + err.message);
  } finally { btn.disabled = false; }
});

// Apagar
document.getElementById('bulk-delete').addEventListener('click', () => {
  if (!selected.size) return;
  const n = selected.size;
  document.getElementById('modal-del-info').textContent =
    `Apagar ${n} device${n === 1 ? '' : 's'}? Esta ação não pode ser desfeita.`;
  show('modal-del');
});
document.getElementById('modal-del-cancel').addEventListener('click', () => hide('modal-del'));
document.getElementById('modal-del').addEventListener('click', (e) => { if (e.target.id === 'modal-del') hide('modal-del'); });
document.getElementById('modal-del-confirm').addEventListener('click', async () => {
  const btn = document.getElementById('modal-del-confirm');
  btn.disabled = true;
  try {
    const res = await api('/api/devices/bulk/delete', {
      method: 'POST', body: { device_ids: selectedIds() },
    });
    hide('modal-del');
    toast.success(`${res.deleted} device(s) apagado(s)`);
    await afterBulk();
  } catch (err) {
    toast.error('Erro ao apagar: ' + err.message);
  } finally { btn.disabled = false; }
});

document.querySelector('[data-action="force-monitor"]')?.addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try { await api('/api/devices/force-monitor', { method: 'POST' }); toast.success('Coleta iniciada'); setTimeout(load, 5000); }
  catch (err) { toast.error(err.status === 429 ? 'Aguarde para chamar novamente' : 'Falha'); }
  finally { setTimeout(() => { btn.disabled = false; }, 60000); }
});

loadEnvironments();
load();
setInterval(load, 5000);
