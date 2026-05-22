import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

const envId = window.EC_ENV_ID;
let sheet = null;
let pollTimer = null;
let currentRun = null;
let modeloTelefone = '';
let isHtek = false;

// Charset "seguro" para senhas SIP no firmware HTEK. O aparelho aceita o XML
// mas falha o registro silenciosamente se a senha tem chars fora desse conjunto
// ou se passa de ~25 caracteres.
const SENHA_HTEK_SAFE_RE = /^[A-Za-z0-9!#%*+,\-./:=?@_~]*$/;
function senhaProblematica(senha) {
  if (!isHtek || !senha) return null;
  if (senha.length > 25) return `senha com ${senha.length} chars — HTEK aceita mas o registro SIP costuma falhar acima de ~25`;
  if (!SENHA_HTEK_SAFE_RE.test(senha)) return "senha tem chars que o firmware HTEK pode rejeitar (use apenas: A-Z a-z 0-9 ! # % * + , - . / : = ? @ _ ~)";
  return null;
}

const STATUS_META = {
  applied:  { color: "green",  label: "aplicado" },
  outdated: { color: "yellow", label: "desatualizado" },
  pending:  { color: "gray",   label: "pendente" },
  error:    { color: "red",    label: "erro" },
};
const statusLabel = (s) => (STATUS_META[s] || STATUS_META.pending).label;

// type:'hidden' = nao aparece. readOnly:true = aparece mas nao edita.
const COLUMNS = [
  { type: "hidden",   name: "id" },
  { type: "checkbox", name: "_sel",              title: "✓",             width: 36 },
  { type: "text",     name: "ip",                title: "IP",            width: 130 },
  { type: "text",     name: "numero_ramal",      title: "Ramal",         width: 90 },
  { type: "text",     name: "nome_visivel",      title: "Nome visível",  width: 160 },
  { type: "text",     name: "user_auth",         title: "User auth",     width: 110 },
  { type: "text",     name: "senha_sip",         title: "Senha SIP",     width: 130 },
  { type: "text",     name: "servidor_sip",      title: "Servidor SIP",  width: 180 },
  { type: "text",     name: "numero_abreviado",  title: "Nº abreviado",  width: 130 },
  { type: "text",     name: "_status",           title: "Status",        width: 130, readOnly: true },
  { type: "text",     name: "_modelo",           title: "Modelo",        width: 100, readOnly: true },
  { type: "text",     name: "_mac",              title: "MAC",           width: 150, readOnly: true },
  { type: "text",     name: "_ultima",           title: "Última aplic.", width: 160, readOnly: true },
  { type: "text",     name: "_erro",             title: "Erro",          width: 260, readOnly: true },
];
const COL_INDEX = Object.fromEntries(COLUMNS.map((c, i) => [c.name, i]));
const EDITABLE_FIELDS = ["ip","numero_ramal","nome_visivel","user_auth","senha_sip","servidor_sip","numero_abreviado"];

function rowToArray(l) {
  return COLUMNS.map(c => {
    switch (c.name) {
      case "_sel":     return false;
      case "_status":  return statusLabel(l.status || "pending");
      case "_modelo":  return l.ultimo_modelo || "";
      case "_mac":     return l.ultimo_mac || "";
      case "_ultima":  return l.ultima_aplicacao || "";
      case "_erro":    return l.ultimo_erro || "";
      default:         return (l[c.name] != null ? String(l[c.name]) : "");
    }
  });
}

// Smart autofill numerico: Jspreadsheet CE 4.x so detecta padrao se a celula
// for um numero puro. Quando arrastamos textos como "HOST01" ou "192.168.0.10",
// o fill copia o valor identico. Aqui detectamos N>=2 mudancas contiguas iguais
// na mesma coluna e re-aplicamos com incremento do numero final.
let _autofillBusy = false;
function smartAutofill(records) {
  if (_autofillBusy || !records || records.length < 2) return;
  const byCol = {};
  records.forEach(r => {
    const x = parseInt(r.x);
    const y = parseInt(r.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    (byCol[x] = byCol[x] || []).push({ x, y, value: r.newValue ?? r.value });
  });
  const updates = [];
  Object.values(byCol).forEach(changes => {
    if (changes.length < 2) return;
    changes.sort((a, b) => a.y - b.y);
    for (let i = 1; i < changes.length; i++) {
      if (changes[i].y !== changes[i - 1].y + 1) return;
    }
    const first = String(changes[0].value ?? "");
    const allSame = changes.every(c => String(c.value ?? "") === first);
    if (!allSame) return;
    const m = first.match(/^(.*?)(\d+)$/);
    if (!m) return;
    const prefix = m[1];
    const startNum = parseInt(m[2], 10);
    const width = m[2].length;
    changes.forEach((c, i) => {
      if (i === 0) return;
      const next = prefix + String(startNum + i).padStart(width, "0");
      updates.push([c.x, c.y, next]);
    });
  });
  if (!updates.length) return;
  _autofillBusy = true;
  try {
    updates.forEach(([x, y, v]) => sheet.setValueFromCoords(x, y, v, true));
  } finally {
    _autofillBusy = false;
  }
}

function buildSheet(linhas) {
  if (sheet) { try { sheet.destroy(); } catch (_e) {} }
  const container = $('#ec-spreadsheet');
  container.innerHTML = '';
  const data = linhas.map(rowToArray);
  while (data.length < Math.max(linhas.length + 50, 100)) {
    data.push(COLUMNS.map(() => ""));
  }
  sheet = jspreadsheet(container, {
    data,
    columns: COLUMNS,
    tableOverflow: true,
    tableWidth: "100%",
    tableHeight: "calc(100vh - 280px)",
    allowInsertRow: true,
    allowInsertColumn: false,
    allowDeleteColumn: false,
    allowManualInsertRow: true,
    rowDrag: false,
    columnDrag: false,
    defaultColAlign: "left",
    onafterchanges: (_instance, records) => {
      smartAutofill(records);
      refreshSelectedCount();
    },
  });
}

function isTruthyCell(v) {
  if (v === true) return true;
  if (typeof v === "string") return v === "true" || v === "1" || v.toUpperCase() === "TRUE";
  return !!v;
}

function getSelectedIds() {
  if (!sheet) return [];
  const data = sheet.getData();
  const ids = [];
  for (const row of data) {
    if (!isTruthyCell(row[COL_INDEX._sel])) continue;
    const id = row[COL_INDEX.id];
    const ip = row[COL_INDEX.ip];
    if (id && ip) ids.push(String(id));
  }
  return ids;
}

function refreshSelectedCount() {
  const n = getSelectedIds().length;
  const btn = $('#ec-apply-selected');
  if (btn) {
    btn.textContent = `Aplicar selecionados (${n})`;
    btn.disabled = n === 0;
  }
}

function setAllChecked(fn) {
  if (!sheet) return;
  const data = sheet.getData();
  for (let i = 0; i < data.length; i++) {
    const want = fn(data[i], i);
    if (isTruthyCell(data[i][COL_INDEX._sel]) !== !!want) {
      sheet.setValueFromCoords(COL_INDEX._sel, i, !!want, true);
    }
  }
  refreshSelectedCount();
}

function readNonEmptyRows() {
  if (!sheet) return [];
  const data = sheet.getData();
  const out = [];
  for (const row of data) {
    const empty = EDITABLE_FIELDS.every(f => {
      const v = row[COL_INDEX[f]];
      return v == null || v === "";
    });
    if (empty) continue;
    const obj = {};
    COLUMNS.forEach((c, idx) => {
      obj[c.name] = row[idx] == null ? "" : String(row[idx]);
    });
    out.push(obj);
  }
  return out;
}

function collectLinhasForSave() {
  return readNonEmptyRows().map(l => ({
    id: l.id || undefined,
    ip: l.ip || "",
    numero_ramal: l.numero_ramal || "",
    nome_visivel: l.nome_visivel || "",
    user_auth: l.user_auth || "",
    senha_sip: l.senha_sip || "",
    servidor_sip: l.servidor_sip || "",
    numero_abreviado: l.numero_abreviado || "",
  }));
}

function findRowIdxByIp(ip) {
  if (!sheet) return -1;
  const data = sheet.getData();
  for (let i = 0; i < data.length; i++) {
    if (data[i][COL_INDEX.ip] === ip) return i;
  }
  return -1;
}

function setCell(rowIdx, fieldName, value) {
  const colIdx = COL_INDEX[fieldName];
  if (colIdx == null || !sheet) return;
  sheet.setValueFromCoords(colIdx, rowIdx, value, true);
}

function renderStatusPills(linhas) {
  const counts = { applied: 0, outdated: 0, pending: 0, error: 0 };
  linhas.forEach(l => {
    const s = l.status || "pending";
    counts[s] = (counts[s] || 0) + 1;
  });
  const container = $('#ec-status-pills');
  container.innerHTML = "";
  Object.entries(counts).forEach(([k, v]) => {
    if (!v) return;
    const meta = STATUS_META[k];
    const span = document.createElement("span");
    span.className = `inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-${meta.color}-500/15 text-${meta.color}-400 ring-${meta.color}-500/30`;
    span.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-${meta.color}-400"></span>${meta.label} <span class="tabular-nums">${v}</span>`;
    container.appendChild(span);
  });
}

async function reload() {
  const env = await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}`);
  modeloTelefone = env.modelo_telefone || '';
  isHtek = String(modeloTelefone).toLowerCase().startsWith("htek");
  $('#ec-title').textContent = env.nome;
  $('#ec-subtitle').textContent = `${modeloTelefone} · ${env.linhas.length} ramal${env.linhas.length === 1 ? '' : 'is'}`;
  $('#ec-config-link').href = `/extension-configurator/environments/${encodeURIComponent(envId)}/config`;
  renderStatusPills(env.linhas);
  buildSheet(env.linhas);
  refreshSelectedCount();
}

async function save() {
  try {
    const linhas = collectLinhasForSave();
    const env = await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}/lines`, {
      method: 'PUT', body: { linhas },
    });
    toast.success(`${env.linhas.length} linha(s) salvas`);
    // refresca IDs + status sem perder posicao da planilha
    env.linhas.forEach((l, i) => {
      setCell(i, "id", l.id);
      setCell(i, "_status", statusLabel(l.status || "pending"));
    });
    renderStatusPills(env.linhas);
    return env;
  } catch (e) {
    toast.error('Erro ao salvar: ' + e.message);
    throw e;
  }
}

function setApplyButtonsDisabled(v) {
  $('#ec-apply').disabled = v;
  const sel = $('#ec-apply-selected');
  sel.disabled = v || getSelectedIds().length === 0;
}

async function apply({ selectedIds = null } = {}) {
  try { await save(); } catch (_e) { return; }
  const force = $('#ec-force').checked;

  // Avisos de senha SIP HTEK (firmware aceita XML mas registro pode falhar)
  if (isHtek) {
    const avisos = [];
    readNonEmptyRows().forEach((l, i) => {
      if (selectedIds && !selectedIds.includes(l.id)) return;
      const aviso = senhaProblematica(l.senha_sip);
      if (aviso) avisos.push(`linha ${i + 1} (${l.ip || "sem ip"}): ${aviso}`);
    });
    if (avisos.length) {
      const ok = confirm(
        `${avisos.length} linha(s) com senha SIP potencialmente problemática para HTEK:\n\n` +
        avisos.slice(0, 5).join("\n") +
        (avisos.length > 5 ? `\n... e mais ${avisos.length - 5}` : "") +
        "\n\nO XML será aceito pelo aparelho, mas o ramal pode não registrar no PBX.\nContinuar mesmo assim?"
      );
      if (!ok) return;
    }
  }

  let confirmMsg;
  if (selectedIds && selectedIds.length) {
    confirmMsg = `Aplicar config em ${selectedIds.length} aparelho(s) selecionado(s)? O telefone reinicia.`;
  } else if (force) {
    confirmMsg = "Forçar reaplicação em TODOS os aparelhos com IP? O telefone reinicia.";
  } else {
    confirmMsg = "Aplicar config nos aparelhos pendentes/desatualizados? O telefone reinicia.";
  }
  if (!confirm(confirmMsg)) return;

  setApplyButtonsDisabled(true);
  try {
    const body = selectedIds && selectedIds.length ? { selected_ids: selectedIds } : {};
    const r = await api(
      `/api/extension-configurator/environments/${encodeURIComponent(envId)}/apply?force=${force ? 1 : 0}`,
      { method: 'POST', body },
    );
    if (r.total === 0) {
      const msg = selectedIds && selectedIds.length
        ? "Nada a aplicar — nenhuma das linhas selecionadas precisa ser enviada."
        : "Nada a aplicar — tudo em dia. Marque 'Forçar' para reaplicar.";
      toast.info(msg);
      setApplyButtonsDisabled(false);
      return;
    }
    toast.success(`Aplicando ${r.total} ramal${r.total === 1 ? '' : 'is'}…`);
    currentRun = r.run_id;
    pollRun();
  } catch (e) {
    toast.error('Erro: ' + e.message);
    setApplyButtonsDisabled(false);
  }
}

async function pollRun() {
  if (!currentRun) return;
  try {
    const s = await api(`/api/extension-configurator/runs/${currentRun}/live`);
    $('#ec-run-panel').classList.remove('hidden');
    const sm = s.summary || {};
    $('#ec-run-summary').textContent =
      `ping ${sm.ping || 0} · send ${sm.send || 0} · ok ${sm.done || 0} · erro ${sm.error || 0} · pend ${sm.pending || 0}`;
    $('#ec-run-rows').innerHTML = (s.rows || []).map((r) => `
      <div class="flex items-center gap-2 py-1 border-b border-gray-700/40">
        <span class="font-mono text-gray-300 w-32 truncate">${esc(r.ip)}</span>
        <span class="text-gray-400 w-24">${esc(r.numero_ramal)}</span>
        <span class="text-gray-200 w-20">${esc(r.stage)}</span>
        <span class="text-gray-500 flex-1 truncate">${esc(r.msg)}</span>
      </div>`).join('');

    // Atualiza status/erro na planilha em tempo real.
    // Stages intermediarios (ping/send) mostram "aplicando..." em vez de
    // "desatualizado" para evitar piscar status incorreto na UI.
    (s.rows || []).forEach(r => {
      const idx = findRowIdxByIp(r.ip);
      if (idx < 0) return;
      if (r.stage === "done") {
        setCell(idx, "_status", statusLabel("applied"));
        setCell(idx, "_erro", "");
      } else if (r.stage === "error") {
        setCell(idx, "_status", statusLabel("error"));
        setCell(idx, "_erro", r.msg || "");
      } else if (r.stage === "ping" || r.stage === "send") {
        setCell(idx, "_status", "aplicando…");
      }
    });

    if (s.finished_at) {
      currentRun = null;
      clearInterval(pollTimer); pollTimer = null;
      toast.info(`Aplicação finalizada · ok ${sm.done || 0} · erro ${sm.error || 0}`);
      setApplyButtonsDisabled(false);
      await reload();
    }
  } catch (_e) { /* swallow */ }
}

// --- handlers ---
$('#ec-save').addEventListener('click', save);
$('#ec-apply').addEventListener('click', () => apply());
$('#ec-apply-selected').addEventListener('click', () => {
  const ids = getSelectedIds();
  if (!ids.length) return;
  apply({ selectedIds: ids });
});
$('#ec-sel-all').addEventListener('click', () => {
  setAllChecked(row => !!row[COL_INDEX.id] && !!row[COL_INDEX.ip]);
});
$('#ec-sel-none').addEventListener('click', () => setAllChecked(() => false));
$('#ec-sel-errors').addEventListener('click', () => {
  const errLabel = statusLabel("error");
  const pendLabel = statusLabel("pending");
  const outLabel = statusLabel("outdated");
  setAllChecked(row => {
    if (!row[COL_INDEX.id] || !row[COL_INDEX.ip]) return false;
    const st = row[COL_INDEX._status];
    return st === errLabel || st === pendLabel || st === outLabel;
  });
});
$('#ec-run-cancel').addEventListener('click', async () => {
  if (!currentRun) return;
  try {
    await api(`/api/extension-configurator/runs/${currentRun}/cancel`, { method: 'POST' });
    toast.info('Cancelamento solicitado');
  } catch (e) { toast.error(e.message); }
});

pollTimer = setInterval(() => { if (currentRun) pollRun(); }, 1500);

reload().catch((e) => toast.error('Falha ao carregar: ' + e.message));
