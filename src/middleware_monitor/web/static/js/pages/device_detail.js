import { api } from '/static/js/api.js';
import { latencyChart } from '/static/js/components/charts.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';

const id = location.pathname.split('/').pop();
let window_ = '24h';

function badge(tone, text, dot = true) {
  const tones = { green: 'bg-green-500/15 text-green-400 ring-green-500/30', red: 'bg-red-500/15 text-red-400 ring-red-500/30', blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30', yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30', gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30' };
  const dots = { green: 'bg-green-400', red: 'bg-red-400', blue: 'bg-blue-400', yellow: 'bg-yellow-400', gray: 'bg-gray-400' };
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${tones[tone]}">${dot ? `<span class="w-1.5 h-1.5 rounded-full ${dots[tone]}"></span>` : ''}${text}</span>`;
}

async function load() {
  try {
    const [d, hist, pings] = await Promise.all([
      api('/api/devices/' + id),
      api(`/api/devices/${id}/history?window=${window_}`),
      api(`/api/devices/${id}/pings?limit=20`),
    ]);
    document.getElementById('dd-title').textContent = `Ramal ${d.name}`;
    document.getElementById('dd-subtitle').textContent = `${d.ip || '—'} · ${d.model || '—'}`;
    document.getElementById('dd-cards').innerHTML = `
      <div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Status lógico</div>
        <div class="mt-2">${d.logical_status === 'available' ? badge('blue', 'disponível') : d.logical_status === 'unavailable' ? badge('yellow', 'indisponível') : badge('gray', '—')}</div>
        <div class="text-xs text-gray-500 mt-2">Último seen ${d.last_seen_at || '—'}</div>
      </div>
      <div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Rede</div>
        <div class="mt-2">${d.network_status === 'online' ? badge('green', 'online') : d.network_status === 'offline' ? badge('red', 'offline') : badge('gray', '—')}</div>
        <div class="text-xs text-gray-500 mt-2">Último ping ${d.last_ping_at || '—'}</div>
      </div>
      <div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Latência atual</div>
        <div class="text-2xl font-bold text-blue-300 mt-1 tabular-nums">${d.latency_ms ?? '—'}<span class="text-sm text-gray-500 ml-1">ms</span></div>
      </div>
      <div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">MAC</div>
        <div class="font-mono text-sm text-gray-200 mt-2">${d.mac || '—'}</div>
        <div class="text-xs text-gray-500 mt-1">Detectado por ARP</div>
      </div>`;

    document.getElementById('dd-chart-info').textContent = `${hist.granularity} · ${hist.points.length} pontos`;
    const points = hist.points.map((p) => ({ ms: p.online_ratio === 0 ? null : p.latency_ms_avg, online: p.online_ratio > 0 }));
    latencyChart(document.getElementById('dd-chart'), points);

    document.getElementById('dd-pings').innerHTML = pings.map((p) => `
      <tr class="hover:bg-gray-700/30">
        <td class="px-4 py-2 text-gray-300 font-mono text-xs">${(p.timestamp || '').replace('T', ' ').slice(0, 19)}</td>
        <td class="px-4 py-2">${p.online ? badge('green', 'online') : badge('red', 'offline')}</td>
        <td class="px-4 py-2 text-right font-mono tabular-nums text-gray-300">${p.latency_ms != null ? p.latency_ms + ' ms' : '—'}</td>
      </tr>
    `).join('');
    injectIcons();
  } catch (_e) {}
}

document.querySelectorAll('#dd-window button').forEach((b) => {
  b.addEventListener('click', () => {
    document.querySelectorAll('#dd-window button').forEach((x) => {
      x.classList.remove('bg-blue-500', 'text-white');
      x.classList.add('text-gray-400', 'hover:text-gray-200');
    });
    b.classList.add('bg-blue-500', 'text-white');
    b.classList.remove('text-gray-400', 'hover:text-gray-200');
    window_ = b.dataset.w;
    load();
  });
});

document.querySelector('[data-action="ping-now"]')?.addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try { await api(`/api/devices/${id}/refresh`, { method: 'POST' }); toast.success('Ping disparado'); load(); }
  catch (err) { toast.error('Falha'); }
  finally { btn.disabled = false; }
});

load();
setInterval(load, 10000);
