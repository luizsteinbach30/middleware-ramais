import { api, qs } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const state = { type: 'all', search: '', page: 1, size: 50, selected: null };
let lastPayload = null;

function badge(tone, text) {
  const tones = { blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30', indigo: 'bg-indigo-500/15 text-indigo-300 ring-indigo-500/30', gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30' };
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${tones[tone]}">${text}</span>`;
}

async function loadList() {
  const data = await api('/api/collections' + qs({ type: state.type, page: state.page, size: state.size }));
  document.getElementById('col-subtitle').textContent = `${data.total} snapshots`;
  const list = document.getElementById('col-list');
  if (!data.items.length) {
    list.innerHTML = `<div class="px-4 py-8 text-center text-sm text-gray-500">Sem coletas ainda.</div>`;
    return;
  }
  list.innerHTML = data.items.map((c) => `
    <button data-id="${c.id}" class="w-full text-left px-4 py-3 hover:bg-gray-700/30 transition ${state.selected === c.id ? 'bg-blue-500/10 ring-1 ring-inset ring-blue-500/30' : ''}">
      <div class="flex items-center justify-between">
        <div class="font-mono text-xs text-gray-300">${c.collected_at}</div>
        ${badge(c.type === 'extensions' ? 'blue' : 'indigo', c.type)}
      </div>
      <div class="flex items-center justify-between mt-1.5">
        <div class="text-xs text-gray-500">id #${c.id} · <span class="font-mono">${c.payload_hash}</span></div>
        <div class="text-xs text-gray-500 tabular-nums">${(c.size_bytes / 1024).toFixed(1)} KB</div>
      </div>
    </button>
  `).join('');
  list.querySelectorAll('[data-id]').forEach((b) =>
    b.addEventListener('click', () => { state.selected = parseInt(b.dataset.id, 10); loadList(); loadDetail(); })
  );
  document.getElementById('col-pageinfo').textContent = `${data.page} / ${Math.max(1, Math.ceil(data.total / data.size))} · ${data.total}`;
  document.getElementById('col-prev').disabled = data.page <= 1;
  document.getElementById('col-next').disabled = data.page * data.size >= data.total;
  if (state.selected == null && data.items[0]) { state.selected = data.items[0].id; loadDetail(); }
}

async function loadDetail() {
  if (state.selected == null) return;
  const d = await api('/api/collections/' + state.selected);
  lastPayload = d.payload;
  document.getElementById('col-meta').textContent = `id #${d.id} · ${d.collected_at}`;
  document.getElementById('col-type-badge').innerHTML = badge(d.type === 'extensions' ? 'blue' : 'indigo', d.type);
  document.getElementById('col-viewer').textContent = JSON.stringify(d.payload, null, 2);
}

document.getElementById('col-type').addEventListener('change', (e) => { state.type = e.target.value; state.page = 1; loadList(); });
document.getElementById('col-prev').addEventListener('click', () => { if (state.page > 1) { state.page--; loadList(); } });
document.getElementById('col-next').addEventListener('click', () => { state.page++; loadList(); });
document.querySelector('[data-action="copy"]').addEventListener('click', async () => {
  if (!lastPayload) return;
  await navigator.clipboard.writeText(JSON.stringify(lastPayload, null, 2)); toast.success('Copiado.');
});
document.querySelector('[data-action="download"]').addEventListener('click', () => {
  if (!lastPayload) return;
  const blob = new Blob([JSON.stringify(lastPayload, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `collection-${state.selected}.json`;
  a.click();
});

loadList();
