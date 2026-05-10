import { api } from '/static/js/api.js';
import { multiLineChart } from '/static/js/components/charts.js';
import { toast } from '/static/js/components/toast.js';
import { injectIcons } from '/static/js/components/icons.js';

const KPI = [
  { key: 'total', label: 'Devices', tone: 'gray', hint: 'Total monitorado' },
  { key: 'network_online', label: 'Rede online', tone: 'green', hint: 'Respondendo ping' },
  { key: 'network_offline', label: 'Rede offline', tone: 'red', hint: 'Sem resposta' },
  { key: 'logical_available', label: 'Lóg. disponíveis', tone: 'blue', hint: 'Status USCall' },
  { key: 'logical_unavailable', label: 'Lóg. indisponíveis', tone: 'yellow', hint: 'USCall reportou falha' },
  { key: 'avg_latency_ms', label: 'Latência média', tone: 'indigo', suffix: ' ms', hint: 'Atual' },
];
const TONE = { green: 'text-green-400', red: 'text-red-400', blue: 'text-blue-400', yellow: 'text-yellow-400', indigo: 'text-indigo-300', gray: 'text-gray-100' };

async function load() {
  try {
    const [s, ts, ver] = await Promise.all([
      api('/api/dashboard/summary'),
      api('/api/dashboard/timeseries?window_hours=24'),
      api('/api/system/version'),
    ]);
    document.getElementById('hdr-subtitle').textContent =
      `${s.total} ramais · última coleta ${s.last_collection_at || '—'}`;
    const grid = document.getElementById('kpis');
    grid.innerHTML = KPI.map((k) => `
      <div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl p-4 hover:ring-gray-600 transition">
        <div class="text-[11px] uppercase tracking-wider text-gray-400 font-semibold">${k.label}</div>
        <div class="text-2xl font-bold mt-1 ${TONE[k.tone]}">${s[k.key] ?? '—'}${k.suffix || ''}</div>
        <div class="text-xs text-gray-500 mt-1">${k.hint}</div>
      </div>
    `).join('');

    document.getElementById('wh-total').textContent = s.webhooks_24h;
    document.getElementById('wh-badges').innerHTML = `
      <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-green-500/15 text-green-400 ring-green-500/30"><span class="w-1.5 h-1.5 rounded-full bg-green-400"></span>${s.webhooks_24h_ok} ok</span>
      <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-red-500/15 text-red-400 ring-red-500/30"><span class="w-1.5 h-1.5 rounded-full bg-red-400"></span>${s.webhooks_24h_fail} falha</span>`;
    if (s.webhooks_24h > 0) {
      document.getElementById('wh-bar-ok').style.width = (s.webhooks_24h_ok / s.webhooks_24h * 100) + '%';
      document.getElementById('wh-bar-fail').style.width = (s.webhooks_24h_fail / s.webhooks_24h * 100) + '%';
    }

    document.getElementById('sys-next').innerHTML = ver.available_version
      ? `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-blue-500/15 text-blue-400 ring-blue-500/30">${ver.available_version} disponível</span>`
      : '—';
    document.getElementById('sys-check').textContent = ver.last_check_at || '—';
    document.getElementById('sys-auto').innerHTML = ver.auto_update
      ? `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-green-500/15 text-green-400 ring-green-500/30">Ativado</span>`
      : `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-gray-500/15 text-gray-400 ring-gray-500/30">Desligado</span>`;

    const c = document.getElementById('chart-online-offline');
    multiLineChart(c, [
      { color: '#34d399', data: ts.map((p) => p.online) },
      { color: '#f87171', data: ts.map((p) => p.offline) },
    ]);
    injectIcons();
  } catch (e) {
    if (e.message !== 'unauthorized') toast.error('Falha ao carregar dashboard');
  }
}

document.querySelector('[data-action="refresh"]')?.addEventListener('click', load);
document.querySelector('[data-action="force-monitor"]')?.addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    await api('/api/devices/force-monitor', { method: 'POST' });
    toast.success('Coleta forçada. Aguarde alguns segundos…');
    setTimeout(load, 5000);
  } catch (err) {
    toast.error(err.status === 429 ? 'Aguarde para chamar novamente' : 'Falha ao forçar coleta');
  } finally {
    setTimeout(() => { btn.disabled = false; }, 60000);
  }
});

load();
setInterval(load, 5000);
