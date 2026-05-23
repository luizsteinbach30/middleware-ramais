import { api, qs } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

const state = {
  search: '', network: 'all', logical: 'all', page: 1, size: 50,
};

function syncFromUrl() {
  const p = new URLSearchParams(location.search);
  state.search = p.get('search') || '';
  state.network = p.get('network') || 'all';
  state.logical = p.get('logical') || 'all';
  state.page = parseInt(p.get('page') || '1', 10);
}

function syncToUrl() {
  const p = new URLSearchParams();
  if (state.search) p.set('search', state.search);
  if (state.network !== 'all') p.set('network', state.network);
  if (state.logical !== 'all') p.set('logical', state.logical);
  if (state.page > 1) p.set('page', String(state.page));
  history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p : ''));
}

function badge(tone, text, dot=false) {
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

    const tbody = document.getElementById('devices-tbody');
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="10" class="px-4 py-12 text-center text-sm text-gray-500">Nenhum device com esses filtros.</td></tr>`;
    } else {
      tbody.innerHTML = data.items.map((d) => {
        const linkCell = d.extension_environment_id
          ? `<a href="/extension-configurator/environments/${d.extension_environment_id}" class="text-blue-400 hover:underline text-xs" onclick="event.stopPropagation()">${d.extension_environment_nome}</a>
             <div class="text-[11px] text-gray-500">ramal ${d.extension_line_ramal || '—'}${d.extension_line_nome_visivel ? ' · ' + d.extension_line_nome_visivel : ''}</div>`
          : `<span class="text-xs text-gray-500">— sem vínculo</span>`;
        return `
        <tr class="hover:bg-gray-700/30 transition cursor-pointer" data-id="${d.id}">
          <td class="px-4 py-2.5 font-bold text-gray-100">${d.name}</td>
          <td class="px-4 py-2.5 font-mono text-xs text-gray-300">${d.ip || '—'}</td>
          <td class="px-4 py-2.5 font-mono text-xs text-gray-500">${d.mac || '—'}</td>
          <td class="px-4 py-2.5 text-gray-400">${d.model || '—'}</td>
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
          if (e.target.closest('button,a')) return;
          location.href = '/devices/' + tr.dataset.id;
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
    injectIcons();
  } catch (_e) {}
}

const debounced = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const search = document.getElementById('f-search');
const network = document.getElementById('f-network');
const logical = document.getElementById('f-logical');

syncFromUrl();
search.value = state.search;
network.value = state.network;
logical.value = state.logical;
search.addEventListener('input', debounced((e) => { state.search = e.target.value; state.page = 1; load(); }));
network.addEventListener('change', (e) => { state.network = e.target.value; state.page = 1; load(); });
logical.addEventListener('change', (e) => { state.logical = e.target.value; state.page = 1; load(); });
document.getElementById('f-clear').addEventListener('click', () => {
  state.search = ''; state.network = 'all'; state.logical = 'all'; state.page = 1;
  search.value = ''; network.value = 'all'; logical.value = 'all'; load();
});
document.getElementById('page-prev').addEventListener('click', () => { if (state.page > 1) { state.page--; load(); } });
document.getElementById('page-next').addEventListener('click', () => { state.page++; load(); });

document.querySelector('[data-action="force-monitor"]')?.addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try { await api('/api/devices/force-monitor', { method: 'POST' }); toast.success('Coleta iniciada'); setTimeout(load, 5000); }
  catch (err) { toast.error(err.status === 429 ? 'Aguarde para chamar novamente' : 'Falha'); }
  finally { setTimeout(() => { btn.disabled = false; }, 60000); }
});

load();
setInterval(load, 5000);
