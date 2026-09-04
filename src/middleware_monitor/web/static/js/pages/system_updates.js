import { api } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

function badge(tone, text, dot = false) {
  const tones = { green: 'bg-green-500/15 text-green-400 ring-green-500/30', red: 'bg-red-500/15 text-red-400 ring-red-500/30', yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30', blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30', gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30' };
  const dots = { green: 'bg-green-400', red: 'bg-red-400', yellow: 'bg-yellow-400', blue: 'bg-blue-400', gray: 'bg-gray-400' };
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${tones[tone]}">${dot ? `<span class="w-1.5 h-1.5 rounded-full ${dots[tone]}"></span>` : ''}${text}</span>`;
}

const $ = (s) => document.getElementById(s);

const DIA_LABEL = {
  mon: 'seg', tue: 'ter', wed: 'qua', thu: 'qui', fri: 'sex', sat: 'sáb', sun: 'dom',
};

// Dias marcados vivem aqui: os chips são desenhados a cada render.
let diasSelecionados = new Set();

function renderDias(dias) {
  const box = $('upd-days');
  box.innerHTML = dias.map((d) => {
    const on = diasSelecionados.has(d);
    const cls = on
      ? 'bg-blue-500/20 text-blue-200 ring-blue-500/50'
      : 'bg-gray-900 text-gray-400 ring-gray-700 hover:text-gray-200';
    return `<button type="button" data-dia="${d}" aria-pressed="${on}"
      class="px-3 py-1.5 rounded-lg text-xs font-medium ring-1 ring-inset transition-colors ${cls}">${DIA_LABEL[d] || d}</button>`;
  }).join('');
  box.querySelectorAll('button[data-dia]').forEach((b) => {
    b.addEventListener('click', () => {
      const d = b.dataset.dia;
      if (diasSelecionados.has(d)) diasSelecionados.delete(d); else diasSelecionados.add(d);
      renderDias(dias);
    });
  });
}

function descreveAgendamento(cfg) {
  if (!cfg.auto_check) return 'Verificação automática desligada — use "Verificar agora".';
  const hh = String(cfg.check_hour).padStart(2, '0');
  const mm = String(cfg.check_minute).padStart(2, '0');
  const todos = cfg.check_days.length === 7;
  const dias = todos ? 'todos os dias' : cfg.check_days.map((d) => DIA_LABEL[d] || d).join(', ');
  return `Próxima verificação: ${hh}:${mm} (${cfg.timezone_offset}) · ${dias}`;
}

async function loadUpdateSettings() {
  const cfg = await api('/api/system/update-settings');
  $('upd-auto').checked = cfg.auto_check;
  $('upd-auto-label').textContent = cfg.auto_check
    ? 'Avisa quando houver versão nova' : 'Desligada';
  $('upd-channel').value = cfg.channel;
  $('upd-hour').value = cfg.check_hour;
  $('upd-minute').value = cfg.check_minute;
  $('upd-tz').textContent = `(${cfg.timezone_offset} — horário do servidor)`;
  diasSelecionados = new Set(cfg.check_days);
  renderDias(cfg.weekdays);
  $('upd-next-check').textContent = descreveAgendamento(cfg);
  return cfg;
}

async function load() {
  const ver = await api('/api/system/version');
  document.getElementById('upd-current').textContent = ver.current;
  document.getElementById('upd-channel-badge').outerHTML = `<span id="upd-channel-badge">${badge(ver.channel === 'beta' ? 'yellow' : 'green', ver.channel)}</span>`;
  document.getElementById('upd-last-check').textContent = `Último check: ${fmtTs(ver.last_check_at)}`;
  await loadUpdateSettings();

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
      <td class="px-4 py-2.5 font-mono text-xs text-gray-300">${fmtTs(u.timestamp)}</td>
      <td class="px-4 py-2.5 font-mono text-xs text-gray-400">${u.from_version}</td>
      <td class="px-4 py-2.5 font-mono text-xs text-gray-200 font-semibold">${u.to_version}</td>
      <td class="px-4 py-2.5">${badge(u.channel === 'beta' ? 'yellow' : 'gray', u.channel)}</td>
      <td class="px-4 py-2.5">${u.status === 'success' ? badge('green', 'Sucesso', true) : u.status === 'rolled_back' ? badge('yellow', 'Rollback', true) : badge('red', 'Falha', true)}</td>
      <td class="px-4 py-2.5 text-right font-mono tabular-nums text-gray-300 text-xs">${(u.duration_ms / 1000).toFixed(1)}s</td>
    </tr>
  `).join('');
  injectIcons();
}

$('upd-cfg-save').addEventListener('click', async () => {
  const btn = $('upd-cfg-save');
  if (!diasSelecionados.size) {
    toast.error('Marque ao menos um dia da semana (ou desligue a verificação).');
    return;
  }
  btn.disabled = true;
  try {
    const cfg = await api('/api/system/update-settings', {
      method: 'PUT',
      body: {
        auto_check: $('upd-auto').checked,
        channel: $('upd-channel').value,
        check_hour: parseInt($('upd-hour').value || '0', 10),
        check_minute: parseInt($('upd-minute').value || '0', 10),
        check_days: [...diasSelecionados],
      },
    });
    $('upd-next-check').textContent = descreveAgendamento(cfg);
    toast.success('Verificação automática atualizada');
    load();
  } catch (e) {
    toast.error('Falha ao salvar: ' + e.message);
  } finally {
    btn.disabled = false;
  }
});

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
    const r = await api('/api/system/update', { method: 'POST' });
    if (r && r.mode === 'systemd') toast.success('Atualização pedida ao sistema. O serviço reinicia em instantes.');
    else toast.success('Update agendado. O serviço pode reiniciar.');
  } catch { toast.error('Falha'); }
});

load();
