import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function row(r) {
  const href = `/extension-configurator/runs/${encodeURIComponent(r.id)}`;
  const falhaCls = r.falha ? 'text-red-400' : 'text-gray-500';
  const forcado = r.forcado
    ? '<span class="text-yellow-400">sim</span>'
    : '<span class="text-gray-500">não</span>';
  return `<tr class="cursor-pointer hover:bg-gray-700/30" data-href="${esc(href)}">
    <td class="px-4 py-2 font-mono text-xs text-gray-300">${esc(r.environment_id)}</td>
    <td class="px-4 py-2 text-xs text-gray-400">${r.started_at ? esc(fmtTs(r.started_at)) : '—'}</td>
    <td class="px-4 py-2 text-xs text-gray-400">${r.finished_at ? esc(fmtTs(r.finished_at)) : '—'}</td>
    <td class="px-4 py-2 text-right text-xs text-gray-200 tabular-nums">${r.total}</td>
    <td class="px-4 py-2 text-right text-xs text-green-400 tabular-nums">${r.ok}</td>
    <td class="px-4 py-2 text-right text-xs ${falhaCls} tabular-nums">${r.falha}</td>
    <td class="px-4 py-2 text-xs text-gray-400">${esc(r.operador || '—')}</td>
    <td class="px-4 py-2 text-xs">${forcado}</td>
    <td class="px-4 py-2 text-xs">
      <a href="${esc(href)}" class="text-blue-400 hover:text-blue-300">abrir →</a>
    </td>
  </tr>`;
}

async function load() {
  try {
    const { runs } = await api('/api/extension-configurator/runs');
    document.getElementById('ec-runs-empty').classList.toggle('hidden', runs.length > 0);
    const tbody = document.getElementById('ec-runs-tbody');
    tbody.innerHTML = runs.map(row).join('');
    tbody.querySelectorAll('tr[data-href]').forEach((tr) => {
      tr.addEventListener('click', (ev) => {
        if (ev.target.tagName === 'A') return;
        location.href = tr.dataset.href;
      });
    });
  } catch (e) {
    toast.error('Falha ao carregar: ' + e.message);
  }
}

load();
