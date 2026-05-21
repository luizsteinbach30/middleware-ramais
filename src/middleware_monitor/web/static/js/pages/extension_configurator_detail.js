import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

const envId = window.EC_ENV_ID;
let sheet = null;
let pollTimer = null;
let currentRun = null;

const COLS = [
  { id: 'id',               label: 'id',              type: 'hidden' },
  { id: 'ip',               label: 'IP',              type: 'text',   width: 130 },
  { id: 'numero_ramal',     label: 'Ramal',           type: 'text',   width: 90 },
  { id: 'nome_visivel',     label: 'Nome',            type: 'text',   width: 160 },
  { id: 'user_auth',        label: 'User auth',       type: 'text',   width: 110 },
  { id: 'senha_sip',        label: 'Senha SIP',       type: 'text',   width: 130 },
  { id: 'servidor_sip',     label: 'Servidor SIP',    type: 'text',   width: 150 },
  { id: 'numero_abreviado', label: 'Nº abreviado',    type: 'text',   width: 110 },
  { id: 'status',           label: 'Status',          type: 'text',   width: 100, readOnly: true },
  { id: 'ultimo_erro',      label: 'Último erro',     type: 'text',   width: 200, readOnly: true },
];

function rowsFromLines(linhas) {
  return linhas.map((ln) => COLS.map((c) => ln[c.id] ?? ''));
}

function buildSheet(linhas) {
  if (sheet) { try { sheet.destroy(); } catch (_e) {} }
  const Container = $('#ec-spreadsheet');
  Container.innerHTML = '';
  const minRows = Math.max(linhas.length + 5, 10);
  sheet = jspreadsheet(Container, {
    data: rowsFromLines(linhas),
    columns: COLS.map((c) => ({
      title: c.label, type: c.type, width: c.width || 120,
      readOnly: !!c.readOnly,
    })),
    minDimensions: [COLS.length, minRows],
    allowInsertRow: true,
    allowDeleteRow: true,
    allowDeleteColumn: false,
    columnSorting: false,
    tableOverflow: true,
    tableHeight: '520px',
  });
}

function collectRows() {
  if (!sheet) return [];
  const rows = sheet.getData();
  return rows
    .map((r) => {
      const o = {};
      COLS.forEach((c, i) => { o[c.id] = r[i] ?? ''; });
      delete o.status; delete o.ultimo_erro; // readonly
      return o;
    })
    .filter((o) => o.ip || o.numero_ramal); // ignora linhas vazias
}

async function reload() {
  const env = await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}`);
  $('#ec-title').textContent = env.nome;
  $('#ec-subtitle').textContent = `${env.modelo_telefone} · ${env.linhas.length} ramal${env.linhas.length === 1 ? '' : 'is'}`;
  $('#ec-config-link').href = `/extension-configurator/environments/${encodeURIComponent(envId)}/config`;
  buildSheet(env.linhas);
}

async function save() {
  const linhas = collectRows();
  try {
    await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}/lines`, {
      method: 'PUT', body: { linhas },
    });
    toast({ tone: 'success', text: 'Planilha salva' });
    await reload();
  } catch (e) {
    toast({ tone: 'error', text: 'Erro ao salvar: ' + e.message });
  }
}

async function apply() {
  // Salva antes para garantir consistência
  await save().catch(() => {});
  try {
    const r = await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}/apply`, {
      method: 'POST', body: {},
    });
    if (r.total === 0) {
      toast({ tone: 'info', text: 'Nada para aplicar — tudo em dia.' });
      return;
    }
    toast({ tone: 'success', text: `Aplicando ${r.total} ramal${r.total === 1 ? '' : 'is'}…` });
    currentRun = r.run_id;
    pollRun();
  } catch (e) {
    toast({ tone: 'error', text: 'Erro: ' + e.message });
  }
}

async function pollRun() {
  if (!currentRun) return;
  try {
    const s = await api(`/api/extension-configurator/runs/${currentRun}/live`);
    $('#ec-run-panel').classList.remove('hidden');
    const sm = s.summary;
    $('#ec-run-summary').textContent =
      `ping ${sm.ping || 0} · send ${sm.send || 0} · ok ${sm.done || 0} · erro ${sm.error || 0} · pend ${sm.pending || 0}`;
    $('#ec-run-rows').innerHTML = s.rows.map((r) => `
      <div class="flex items-center gap-2 py-1 border-b border-gray-700/40">
        <span class="font-mono text-gray-300 w-32 truncate">${esc(r.ip)}</span>
        <span class="text-gray-400 w-24">${esc(r.numero_ramal)}</span>
        <span class="text-gray-200 w-20">${esc(r.stage)}</span>
        <span class="text-gray-500 flex-1 truncate">${esc(r.msg)}</span>
      </div>`).join('');
    if (s.finished_at) {
      currentRun = null;
      clearInterval(pollTimer); pollTimer = null;
      toast({ tone: 'info', text: `Aplicação finalizada · ok ${sm.done || 0} · erro ${sm.error || 0}` });
      await reload();
    }
  } catch (_e) { /* swallow */ }
}

$('#ec-save').addEventListener('click', save);
$('#ec-apply').addEventListener('click', apply);
$('#ec-run-cancel').addEventListener('click', async () => {
  if (!currentRun) return;
  try {
    await api(`/api/extension-configurator/runs/${currentRun}/cancel`, { method: 'POST' });
    toast({ tone: 'info', text: 'Cancelado' });
  } catch (e) { toast({ tone: 'error', text: e.message }); }
});

pollTimer = setInterval(() => { if (currentRun) pollRun(); }, 1500);

reload().catch((e) => toast({ tone: 'error', text: 'Falha ao carregar: ' + e.message }));
