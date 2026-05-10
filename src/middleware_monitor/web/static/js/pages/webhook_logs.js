import { api, qs } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';

const state = { type: 'all', status: 'all', page: 1, size: 50 };

function badge(tone, text, dot=false) {
  const tones = { green: 'bg-green-500/15 text-green-400 ring-green-500/30', red: 'bg-red-500/15 text-red-400 ring-red-500/30', blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30', yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30', gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30', indigo: 'bg-indigo-500/15 text-indigo-300 ring-indigo-500/30' };
  const dots = { green: 'bg-green-400', red: 'bg-red-400', blue: 'bg-blue-400', yellow: 'bg-yellow-400', gray: 'bg-gray-400', indigo: 'bg-indigo-300' };
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${tones[tone]}">${dot ? `<span class="w-1.5 h-1.5 rounded-full ${dots[tone]}"></span>` : ''}${text}</span>`;
}

function httpTone(s) {
  if (!s) return 'gray';
  if (s >= 500) return 'red';
  if (s >= 400) return 'yellow';
  return 'green';
}

async function load() {
  const data = await api('/api/webhook-events' + qs({ type: state.type, status: state.status, page: state.page, size: state.size }));
  const tbody = document.getElementById('wh-tbody');
  if (!data.items.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="px-4 py-12 text-center text-sm text-gray-500">Sem eventos.</td></tr>`;
  } else {
    tbody.innerHTML = data.items.map((w) => `
      <tr class="hover:bg-gray-700/30 ${w.is_test ? 'bg-indigo-500/5' : ''}">
        <td class="px-4 py-2.5 font-mono text-xs text-gray-300">${w.timestamp}</td>
        <td class="px-4 py-2.5">${badge(w.event_type === 'extensions' ? 'blue' : w.event_type === 'devices' ? 'green' : 'indigo', w.event_type)}${w.is_test ? '<span class="ml-2 text-[10px] uppercase tracking-wider text-indigo-300">test</span>' : ''}</td>
        <td class="px-4 py-2.5 text-right">${badge(httpTone(w.http_status), w.http_status || 'ERR')}</td>
        <td class="px-4 py-2.5 text-right font-mono tabular-nums text-gray-300 text-xs">${w.duration_ms} ms</td>
        <td class="px-4 py-2.5">${w.success ? badge('green', 'Sucesso', true) : badge('red', 'Falha', true)}</td>
        <td class="px-4 py-2.5 text-xs text-gray-400 font-mono">${w.attempt}/${w.total_attempts}</td>
        <td class="px-4 py-2.5 text-right">
          <div class="inline-flex gap-1">
            <button data-view="${w.id}" class="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100" title="Ver payload"><span data-icon="eye"></span></button>
            <button data-replay="${w.id}" class="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100" title="Reenviar"><span data-icon="refresh"></span></button>
          </div>
        </td>
      </tr>
    `).join('');
    tbody.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => openModal(b.dataset.view)));
    tbody.querySelectorAll('[data-replay]').forEach((b) => b.addEventListener('click', async () => {
      try { await api(`/api/webhook-events/${b.dataset.replay}/replay`, { method: 'POST' }); toast.success('Reenvio agendado.'); load(); }
      catch (e) { toast.error('Falha ao reenviar'); }
    }));
  }
  document.getElementById('wh-pageinfo').textContent = `Página ${data.page} · ${data.items.length} de ${data.total}`;
  document.getElementById('wh-prev').disabled = data.page <= 1;
  document.getElementById('wh-next').disabled = data.page * data.size >= data.total;
  injectIcons();
}

async function openModal(id) {
  try {
    const d = await api('/api/webhook-events/' + id);
    document.getElementById('wh-modal-meta').textContent = `id #${d.id} · ${d.timestamp}`;
    document.getElementById('wh-modal-content').textContent = JSON.stringify({ payload: d.payload, response: d.response_body }, null, 2);
    document.getElementById('wh-modal').classList.remove('hidden');
  } catch (e) { toast.error('Falha ao carregar payload'); }
}

document.getElementById('wh-f-type').addEventListener('change', (e) => { state.type = e.target.value; state.page = 1; load(); });
document.getElementById('wh-f-status').addEventListener('change', (e) => { state.status = e.target.value; state.page = 1; load(); });
document.getElementById('wh-prev').addEventListener('click', () => { if (state.page > 1) { state.page--; load(); } });
document.getElementById('wh-next').addEventListener('click', () => { state.page++; load(); });
document.querySelectorAll('[data-test-type]').forEach((b) => b.addEventListener('click', async () => {
  try {
    const r = await api('/api/webhooks/test/' + b.dataset.testType, { method: 'POST' });
    toast[r.ok ? 'success' : 'error'](r.ok ? `Teste ${b.dataset.testType} OK` : 'Teste falhou');
    load();
  } catch (e) { toast.error('Falha ao enviar teste'); }
}));
document.querySelectorAll('#wh-modal [data-close], #wh-modal').forEach((el) => el.addEventListener('click', (e) => {
  if (e.target === el) document.getElementById('wh-modal').classList.add('hidden');
}));

load();
setInterval(load, 10000);
