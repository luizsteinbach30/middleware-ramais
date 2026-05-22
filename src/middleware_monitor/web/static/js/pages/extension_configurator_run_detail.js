import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

const runId = window.EC_RUN_ID;

const STATUS_META = {
  applied:  { color: "green",  label: "aplicado" },
  outdated: { color: "yellow", label: "desatualizado" },
  pending:  { color: "gray",   label: "pendente" },
  error:    { color: "red",    label: "erro" },
};

function statusBadge(status) {
  const m = STATUS_META[status] || STATUS_META.pending;
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium ring-1 ring-inset bg-${m.color}-500/15 text-${m.color}-400 ring-${m.color}-500/30">
    <span class="w-1.5 h-1.5 rounded-full bg-${m.color}-400"></span>${m.label}
  </span>`;
}

function durationText(started, finished) {
  if (!started || !finished) return '—';
  const ms = new Date(finished).getTime() - new Date(started).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const total = Math.round(ms / 1000);
  if (total < 60) return `${total}s`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}m ${s}s`;
}

function row(ln) {
  return `<tr>
    <td class="px-4 py-2 font-mono text-xs text-gray-300">${esc(ln.ip || '—')}</td>
    <td class="px-4 py-2 text-xs text-gray-200">${esc(ln.numero_ramal || '—')}</td>
    <td class="px-4 py-2 text-xs text-gray-300">${esc(ln.nome_visivel || '—')}</td>
    <td class="px-4 py-2 text-xs">${statusBadge(ln.status || 'pending')}</td>
    <td class="px-4 py-2 text-xs text-gray-400">${esc(ln.ultimo_modelo || '—')}</td>
    <td class="px-4 py-2 font-mono text-[11px] text-gray-400">${esc(ln.ultimo_mac || '—')}</td>
    <td class="px-4 py-2 text-xs text-gray-400">${esc(ln.ultima_aplicacao || '—')}</td>
    <td class="px-4 py-2 text-xs ${ln.ultimo_erro ? 'text-red-400' : 'text-gray-500'}">${esc(ln.ultimo_erro || '—')}</td>
  </tr>`;
}

async function load() {
  try {
    const data = await api(`/api/extension-configurator/runs/${encodeURIComponent(runId)}/detail`);
    const r = data.run;
    const env = data.environment;

    if (env) {
      $('#ec-rd-title').textContent = `Relatório #${runId} — ${env.nome}`;
      $('#ec-rd-subtitle').textContent =
        `${env.modelo_telefone} · iniciado em ${r.started_at || '—'}`;
      $('#ec-rd-env-link').href = `/extension-configurator/environments/${encodeURIComponent(env.id)}`;
    } else {
      $('#ec-rd-subtitle').textContent = `Iniciado em ${r.started_at || '—'} (ambiente removido)`;
      $('#ec-rd-env-link').classList.add('hidden');
    }

    $('#ec-rd-total').textContent = r.total ?? 0;
    $('#ec-rd-ok').textContent = r.ok ?? 0;
    $('#ec-rd-falha').textContent = r.falha ?? 0;
    $('#ec-rd-duracao').textContent = durationText(r.started_at, r.finished_at);
    $('#ec-rd-operador').textContent = r.operador || '—';

    const linhas = data.linhas || [];
    $('#ec-rd-empty').classList.toggle('hidden', linhas.length > 0);
    $('#ec-rd-tbody').innerHTML = linhas.map(row).join('');
  } catch (e) {
    toast.error('Falha ao carregar: ' + e.message);
  }
}

load();
