import { api, qs } from '/static/js/api.js';

const state = { level: 'all', module: 'all', search: '', page: 1, size: 100 };
let auto = null;

function badge(tone, text) {
  const tones = { red: 'bg-red-500/15 text-red-400 ring-red-500/30', yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30', blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30', gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30' };
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${tones[tone]}">${text}</span>`;
}
const lvlMap = { DEBUG: 'gray', INFO: 'blue', WARN: 'yellow', ERROR: 'red' };

async function load() {
  const data = await api('/api/logs' + qs(state));
  document.getElementById('logs-subtitle').textContent = `${data.total} entradas`;
  const tbody = document.getElementById('logs-tbody');
  tbody.innerHTML = data.items.map((l) => `
    <tr class="hover:bg-gray-700/30 ${l.level === 'ERROR' ? 'bg-red-500/5' : ''}">
      <td class="px-4 py-2.5 font-mono text-xs text-gray-300">${l.timestamp}</td>
      <td class="px-4 py-2.5">${badge(lvlMap[l.level] || 'gray', l.level)}</td>
      <td class="px-4 py-2.5"><span class="px-2 py-0.5 rounded bg-gray-700/60 text-xs font-mono text-gray-300">${l.module}</span></td>
      <td class="px-4 py-2.5 text-gray-200">${l.message}</td>
      <td class="px-4 py-2.5 text-right">${l.context ? `<button data-ctx='${l.id}' class="text-xs text-blue-400 hover:text-blue-300">ver contexto</button>` : ''}</td>
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-ctx]').forEach((b) => b.addEventListener('click', () => {
    const item = data.items.find((x) => String(x.id) === b.dataset.ctx);
    if (!item) return;
    document.getElementById('logs-modal-title').innerHTML = `Contexto · <span class="font-mono text-gray-400">${item.module}</span>`;
    document.getElementById('logs-modal-content').textContent = item.context || '{}';
    document.getElementById('logs-modal').classList.remove('hidden');
  }));
}

['logs-level', 'logs-module', 'logs-search'].forEach((id) => {
  const el = document.getElementById(id);
  el.addEventListener('change', () => { state.level = document.getElementById('logs-level').value; state.module = document.getElementById('logs-module').value; state.search = document.getElementById('logs-search').value; state.page = 1; load(); });
  el.addEventListener('input', () => { state.search = document.getElementById('logs-search').value; });
});
document.getElementById('logs-auto').addEventListener('change', (e) => {
  if (auto) { clearInterval(auto); auto = null; }
  if (e.target.checked) auto = setInterval(load, 5000);
});
document.querySelectorAll('#logs-modal [data-close], #logs-modal').forEach((el) => el.addEventListener('click', (e) => { if (e.target === el) document.getElementById('logs-modal').classList.add('hidden'); }));

load();
