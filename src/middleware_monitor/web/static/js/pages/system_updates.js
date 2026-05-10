import { api } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';

function badge(tone, text, dot = false) {
  const tones = { green: 'bg-green-500/15 text-green-400 ring-green-500/30', red: 'bg-red-500/15 text-red-400 ring-red-500/30', yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30', blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30', gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30' };
  const dots = { green: 'bg-green-400', red: 'bg-red-400', yellow: 'bg-yellow-400', blue: 'bg-blue-400', gray: 'bg-gray-400' };
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${tones[tone]}">${dot ? `<span class="w-1.5 h-1.5 rounded-full ${dots[tone]}"></span>` : ''}${text}</span>`;
}

async function load() {
  const ver = await api('/api/system/version');
  document.getElementById('upd-current').textContent = ver.current;
  document.getElementById('upd-channel-badge').outerHTML = `<span id="upd-channel-badge">${badge(ver.channel === 'beta' ? 'yellow' : 'green', ver.channel)}</span>`;
  document.getElementById('upd-last-check').textContent = `Último check: ${ver.last_check_at || '—'}`;
  document.getElementById('upd-channel').value = ver.channel;
  document.getElementById('upd-auto').checked = ver.auto_update;
  document.getElementById('upd-auto-label').textContent = ver.auto_update ? 'Ativado' : 'Desligado';

  const card = document.getElementById('upd-available-card');
  if (ver.available_version) {
    card.classList.remove('hidden');
    document.getElementById('upd-available-version').textContent = ver.available_version;
    document.getElementById('upd-available-info').textContent = ver.available_published_at || '';
    document.getElementById('upd-notes').textContent = ver.available_notes || '';
  } else {
    card.classList.add('hidden');
  }

  const hist = await api('/api/system/update-history');
  document.getElementById('upd-history').innerHTML = hist.map((u) => `
    <tr class="hover:bg-gray-700/30">
      <td class="px-4 py-2.5 font-mono text-xs text-gray-300">${u.timestamp}</td>
      <td class="px-4 py-2.5 font-mono text-xs text-gray-400">${u.from_version}</td>
      <td class="px-4 py-2.5 font-mono text-xs text-gray-200 font-semibold">${u.to_version}</td>
      <td class="px-4 py-2.5">${badge(u.channel === 'beta' ? 'yellow' : 'gray', u.channel)}</td>
      <td class="px-4 py-2.5">${u.status === 'success' ? badge('green', 'Sucesso', true) : u.status === 'rolled_back' ? badge('yellow', 'Rollback', true) : badge('red', 'Falha', true)}</td>
      <td class="px-4 py-2.5 text-right font-mono tabular-nums text-gray-300 text-xs">${(u.duration_ms / 1000).toFixed(1)}s</td>
    </tr>
  `).join('');
  injectIcons();
}

document.getElementById('upd-check').addEventListener('click', async () => {
  try { const r = await api('/api/system/check-update', { method: 'POST' });
    toast[r.available ? 'info' : 'success'](r.available ? `Disponível: ${r.available}` : 'Você está na última versão'); load(); }
  catch { toast.error('Falha'); }
});
document.getElementById('upd-apply').addEventListener('click', async () => {
  if (!confirm('Iniciar atualização agora?')) return;
  document.getElementById('upd-progress').classList.remove('hidden');
  const stages = [['Baixando…', 20], ['Verificando SHA256…', 45], ['Extraindo…', 65], ['Migrando DB…', 80], ['Reiniciando…', 95]];
  let i = 0;
  const t = setInterval(() => {
    if (i >= stages.length) { clearInterval(t); return; }
    document.getElementById('upd-stage').textContent = stages[i][0];
    document.getElementById('upd-pct').textContent = stages[i][1] + '%';
    document.getElementById('upd-bar').style.width = stages[i][1] + '%';
    i++;
  }, 1500);
  try {
    await api('/api/system/update', { method: 'POST' });
    toast.success('Update agendado. O serviço pode reiniciar.');
  } catch { toast.error('Falha'); }
});

load();
