import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function row(r) {
  return `<tr>
    <td class="px-4 py-2 font-mono text-xs text-gray-300">${esc(r.environment_id)}</td>
    <td class="px-4 py-2 text-xs text-gray-400">${esc(r.started_at || '—')}</td>
    <td class="px-4 py-2 text-xs text-gray-400">${esc(r.finished_at || '—')}</td>
    <td class="px-4 py-2 text-right text-xs text-gray-200">${r.total}</td>
    <td class="px-4 py-2 text-right text-xs text-green-400">${r.ok}</td>
    <td class="px-4 py-2 text-right text-xs ${r.falha ? 'text-red-400' : 'text-gray-500'}">${r.falha}</td>
    <td class="px-4 py-2 text-xs text-gray-400">${esc(r.operador || '—')}</td>
    <td class="px-4 py-2 text-xs">${r.forcado ? '<span class="text-yellow-400">sim</span>' : '<span class="text-gray-500">não</span>'}</td>
  </tr>`;
}

async function load() {
  try {
    const { runs } = await api('/api/extension-configurator/runs');
    document.getElementById('ec-runs-empty').classList.toggle('hidden', runs.length > 0);
    document.getElementById('ec-runs-tbody').innerHTML = runs.map(row).join('');
  } catch (e) {
    toast({ tone: 'error', text: 'Falha ao carregar: ' + e.message });
  }
}

load();
