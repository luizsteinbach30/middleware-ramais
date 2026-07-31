import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

const envId = window.EC_ENV_ID;
let sheet = null;
let pollTimer = null;
let currentRun = null;
let pollInFlight = false;   // impede pollRun sobreposto quando a API demora
let modeloTelefone = '';
let isHtek = false;
let envNome = '';

// ----- Ciclo de vida do polling do run ----------------------------------------
// O interval é criado a cada apply() e destruído quando o run termina/expira.
// (Bug v2.6.0: um único setInterval era criado no load e morria no fim do 1º
// run — o 2º apply ficava sem polling e a tela "travava" até o F5.)
function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => { if (currentRun) pollRun(); }, 1500);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  pollInFlight = false;
}

// ----- Monitor de ping ao vivo -------------------------------------------------
// Liga/desliga via toggle. Enquanto ligado, pinga os IPs preenchidos na planilha
// a cada N segundos e pinta uma bolinha antes do IP: verde = responde, vermelho =
// sem resposta. Para sozinho ao salvar, aplicar, recarregar ou sair da tela.
let monitorOn = false;
let monitorTimer = null;
let monitorBusy = false;
let monitorAbort = null;           // aborta o fetch em voo ao desligar/sair da tela
let pingRounds = 0;                // nº de rodadas concluídas (sensação de tempo real)
const pingStatusByIp = new Map();  // ip -> true (up) | false (down) | undefined (aguardando)

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
  applied:    { color: "green",  label: "aplicado" },
  registered: { color: "green",  label: "registrado" },
  outdated:   { color: "yellow", label: "desatualizado" },
  pending:    { color: "gray",   label: "pendente" },
  error:      { color: "red",    label: "erro" },
};
const statusLabel = (s) => (STATUS_META[s] || STATUS_META.pending).label;

// type:'hidden' = nao aparece. readOnly:true = aparece mas nao edita.
// Ordem dos campos editaveis pedida pelo usuario:
// IP, NOME VISIVEL, RAMAL, USER AUTH, SENHA SIP, SERVIDOR SIP, ABREVIADO.
// Todos como type:"text" para preservar zeros a esquerda (ex: "00001" != 1).
const COLUMNS = [
  { type: "hidden",   name: "id" },
  { type: "checkbox", name: "_sel",              title: "✓",             width: 36 },
  { type: "text",     name: "_preview",          title: "👁",            width: 40, readOnly: true },
  { type: "text",     name: "ip",                title: "IP",            width: 140 },
  { type: "text",     name: "nome_visivel",      title: "Nome visível",  width: 180 },
  { type: "text",     name: "numero_ramal",      title: "Ramal",         width: 100 },
  { type: "text",     name: "user_auth",         title: "User auth",     width: 110 },
  { type: "text",     name: "senha_sip",         title: "Senha SIP",     width: 130 },
  { type: "text",     name: "servidor_sip",      title: "Servidor SIP",  width: 180 },
  { type: "text",     name: "numero_abreviado",  title: "Nº abreviado",  width: 110 },
  { type: "text",     name: "_device",           title: "Device",        width: 160, readOnly: true },
  { type: "text",     name: "_status",           title: "Status",        width: 130, readOnly: true },
  { type: "text",     name: "_modelo",           title: "Modelo",        width: 100, readOnly: true },
  { type: "text",     name: "_mac",              title: "MAC",           width: 150, readOnly: true },
  { type: "text",     name: "_ultima",           title: "Última aplic.", width: 160, readOnly: true },
  { type: "text",     name: "_erro",             title: "Erro",          width: 260, readOnly: true },
];
const COL_INDEX = Object.fromEntries(COLUMNS.map((c, i) => [c.name, i]));
const EDITABLE_FIELDS = ["ip","nome_visivel","numero_ramal","user_auth","senha_sip","servidor_sip","numero_abreviado"];
// Índices das colunas que o usuário edita. Mudanças fora delas (MAC/status/device
// escritos pelo monitor de ping ou pelo polling de aplicação, checkbox de seleção)
// NÃO devem marcar a planilha como "não salva" nem disparar autosave.
const EDITABLE_COL_IDX = new Set(EDITABLE_FIELDS.map((f) => COL_INDEX[f]));
function recordsTouchEditable(records) {
  if (!Array.isArray(records) || records.length === 0) return false;
  return records.some((r) => EDITABLE_COL_IDX.has(parseInt(r.x)));
}

// Mapas auxiliares: line_id -> { device_id, device_name } e row_idx -> line_id
const lineDeviceMap = new Map();

function deviceCellLabel(l) {
  if (l.device_id && l.device_name) {
    const status = l.device_network_status === 'online' ? '🟢'
      : l.device_network_status === 'offline' ? '🔴' : '⚪';
    return `${status} ${l.device_name}`;
  }
  return '—';
}

function rowToArray(l) {
  if (l.id) {
    lineDeviceMap.set(l.id, l.device_id ? {
      device_id: l.device_id, device_name: l.device_name,
    } : null);
  }
  return COLUMNS.map(c => {
    switch (c.name) {
      case "_sel":     return false;
      case "_preview": return l.id ? "👁" : "";  // ícone clicável só em linha salva
      case "_device":  return deviceCellLabel(l);
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
//
// Important: `records` NAO inclui a celula de origem (so as celulas novas
// preenchidas pelo fill). Por isso o incremento comeca em startNum+1 logo
// na primeira celula do range (corrige bug: arrastar "3" gerava 3,4,5 e nao
// 4,5,6 a partir da celula seguinte).
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
      const next = prefix + String(startNum + 1 + i).padStart(width, "0");
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

// Mascara IPv4 aplicada no <input> durante a edicao da celula.
// Permite apenas digitos e ponto, max 3 digitos por octeto, max 4 octetos.
// Insere ponto automaticamente ao completar 3 digitos no octeto atual.
function maskIPv4OnInput(input) {
  const raw = String(input.value || "");
  let v = raw.replace(/[^\d.]/g, "");
  const parts = v.split(".").slice(0, 4).map(p => p.slice(0, 3));
  const last = parts[parts.length - 1];
  if (parts.length < 4 && last && last.length === 3 && !v.endsWith(".")) {
    parts.push("");
  }
  const out = parts.join(".");
  if (out !== raw) input.value = out;
}

// Flag p/ ignorar smartAutofill quando o batch veio de um paste (Ctrl+V).
// Setado em onbeforepaste; consumido no proximo onafterchanges.
let _pasteInProgress = false;

// Flag p/ ignorar autosave e autofill em mutacoes internas (ex: refresh de id/status pos-save).
let _silentMutation = false;
function setCellSilent(rowIdx, fieldName, value) {
  _silentMutation = true;
  try { setCell(rowIdx, fieldName, value); } finally { _silentMutation = false; }
}

// Indicador visual de autosave no header.
const SAVE_STATUS_CLASSES = {
  dirty:  "text-[11px] font-medium text-amber-400 transition-opacity",
  saving: "text-[11px] font-medium text-blue-400 transition-opacity",
  saved:  "text-[11px] font-medium text-green-400 transition-opacity",
  idle:   "text-[11px] font-medium text-transparent transition-opacity",
};
const SAVE_STATUS_TEXT = {
  dirty:  "• Edição não salva",
  saving: "↻ Salvando…",
  saved:  "✓ Salvo",
  idle:   "",
};
function setSaveStatus(state) {
  const el = $('#ec-save-status');
  if (!el) return;
  el.textContent = SAVE_STATUS_TEXT[state] ?? "";
  el.className = SAVE_STATUS_CLASSES[state] ?? SAVE_STATUS_CLASSES.idle;
}

// Debounce 1200ms: cada edicao reseta o timer; quando o usuario para de
// digitar por 1,2s, dispara save() em modo silencioso (sem toast).
let _autosaveTimer = null;
function scheduleAutosave() {
  setSaveStatus("dirty");
  if (_autosaveTimer) clearTimeout(_autosaveTimer);
  _autosaveTimer = setTimeout(async () => {
    _autosaveTimer = null;
    setSaveStatus("saving");
    try {
      await save({ silent: true });
      setSaveStatus("saved");
      setTimeout(() => { if (!_autosaveTimer) setSaveStatus("idle"); }, 2200);
    } catch (_e) {
      setSaveStatus("dirty");  // mantem o aviso pra usuario perceber falha
    }
  }, 1200);
}

// ----- Preview esmaecido durante drag-fill ------------------------------------
// Estende o smart-autofill numerico para qualquer selecao 2D (multi-coluna e/ou
// multi-linha). Funciona em 2 fases:
//   1) preview: enquanto o usuario arrasta o corner, render ghosts em cada
//      coluna mostrando o valor que sera escrito naquela celula.
//   2) commit: ao soltar, interceptamos cada onbeforechange e substituimos o
//      valor (que o Jspreadsheet ia replicar) pelo numero incrementado.
//
// Padrao de cada coluna eh detectado na ULTIMA linha (y2) da fonte: se acaba
// com digitos → vira sequencia. Senao, replica o valor literal.
let _fillPreview = null;       // { x1, y1, x2, y2, sources, targetY }
let _previewLayer = null;
let _fillPreviewCleanupTimer = null;

function _clearFillPreviewGhosts() {
  if (!_previewLayer) return;
  _previewLayer.innerHTML = "";
}

function _stopFillPreview() {
  _fillPreview = null;
  if (_fillPreviewCleanupTimer) {
    clearTimeout(_fillPreviewCleanupTimer);
    _fillPreviewCleanupTimer = null;
  }
  _clearFillPreviewGhosts();
}

function _columnPattern(x, y2) {
  const lastVal = sheet.getValueFromCoords(x, y2);
  const lastStr = lastVal == null ? "" : String(lastVal);
  const m = lastStr.match(/^(.*?)(\d+)$/);
  if (m) {
    return {
      x,
      hasPattern: true,
      lastVal: lastStr,
      prefix: m[1],
      startNum: parseInt(m[2], 10),
      width: m[2].length,
    };
  }
  return { x, hasPattern: false, lastVal: lastStr };
}

function _previewValueFor(x, y) {
  if (!_fillPreview) return null;
  if (y <= _fillPreview.y2) return null;
  if (x < _fillPreview.x1 || x > _fillPreview.x2) return null;
  const src = _fillPreview.sources.find(s => s.x === x);
  if (!src) return null;
  const offset = y - _fillPreview.y2;
  if (src.hasPattern) {
    return src.prefix + String(src.startNum + offset).padStart(src.width, "0");
  }
  return src.lastVal;  // sem padrao: replica o valor literal
}

function _renderFillGhosts() {
  if (!_fillPreview || !_previewLayer || !sheet) return;
  _clearFillPreviewGhosts();
  const { x1, x2, y2, targetY } = _fillPreview;
  if (targetY <= y2) return;
  for (let x = x1; x <= x2; x++) {
    for (let y = y2 + 1; y <= targetY; y++) {
      const value = _previewValueFor(x, y);
      if (value == null || value === "") continue;
      const td = _previewLayer.parentElement.querySelector(
        `td[data-x="${x}"][data-y="${y}"]`,
      );
      if (!td) continue;
      const r = td.getBoundingClientRect();
      const ghost = document.createElement("div");
      ghost.className = "ec-fill-ghost";
      ghost.style.left = `${r.left}px`;
      ghost.style.top = `${r.top}px`;
      ghost.style.width = `${r.width}px`;
      ghost.style.height = `${r.height}px`;
      ghost.textContent = value;
      _previewLayer.appendChild(ghost);
    }
  }
}

function attachFillPreview(container) {
  // Layer absoluto que segura os ghosts (position:fixed nos ghosts internos
  // — coords sao viewport).
  _previewLayer = document.createElement("div");
  _previewLayer.className = "ec-fill-preview-layer";
  container.appendChild(_previewLayer);

  const onMouseDown = (e) => {
    if (!sheet) return;
    const target = e.target;
    const cursor = (target && getComputedStyle(target).cursor) || "";
    const looksLikeCorner =
      cursor === "crosshair" ||
      (target.classList && (
        target.classList.contains("jexcel_selection_corner") ||
        target.classList.contains("jexcel_corner") ||
        target.classList.contains("corner")
      ));
    if (!looksLikeCorner) return;
    const sc = sheet.selectedCell;
    if (!Array.isArray(sc) || sc.length < 4) return;
    const x1 = Math.min(Number(sc[0]), Number(sc[2]));
    const x2 = Math.max(Number(sc[0]), Number(sc[2]));
    const y1 = Math.min(Number(sc[1]), Number(sc[3]));
    const y2 = Math.max(Number(sc[1]), Number(sc[3]));
    // Computa padrao de cada coluna na linha y2 (ultima linha da fonte).
    const sources = [];
    let anyPattern = false;
    for (let x = x1; x <= x2; x++) {
      const src = _columnPattern(x, y2);
      if (src.hasPattern) anyPattern = true;
      sources.push(src);
    }
    if (!anyPattern) return;  // nada pra prever/substituir
    _fillPreview = { x1, x2, y1, y2, sources, targetY: y2 };
  };

  const onMouseMove = (e) => {
    if (!_fillPreview) return;
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const td = under && under.closest && under.closest("td");
    if (!td || td.dataset.x == null || td.dataset.y == null) return;
    const y = parseInt(td.dataset.y);
    if (Number.isNaN(y)) return;
    if (y <= _fillPreview.y2) {
      _fillPreview.targetY = _fillPreview.y2;
      _clearFillPreviewGhosts();
      return;
    }
    _fillPreview.targetY = y;
    _renderFillGhosts();
  };

  const onMouseUp = () => {
    _clearFillPreviewGhosts();
    // NAO limpa _fillPreview imediatamente: o Jspreadsheet vai disparar
    // onbeforechange logo em seguida (ja com o batch de fill), e precisamos
    // do state ativo pra substituir cada valor. Limpamos por timeout de
    // seguranca caso onafterchanges nao dispare (drag sem range valido).
    if (_fillPreviewCleanupTimer) clearTimeout(_fillPreviewCleanupTimer);
    _fillPreviewCleanupTimer = setTimeout(() => {
      _fillPreview = null;
      _fillPreviewCleanupTimer = null;
    }, 200);
  };

  container.addEventListener("mousedown", onMouseDown, true);
  document.addEventListener("mousemove", onMouseMove, true);
  document.addEventListener("mouseup", onMouseUp, true);
  document.addEventListener("dragend", onMouseUp, true);
}

function buildSheet(linhas) {
  if (sheet) { try { sheet.destroy(); } catch (_e) {} }
  const container = $('#ec-spreadsheet');
  container.innerHTML = '';
  _stopFillPreview();
  _previewLayer = null;
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
    onbeforepaste: (_instance, pasted) => {
      // Sinaliza que a proxima rodada de mudancas vem de paste (Ctrl+V):
      // o smartAutofill numerico precisa ser pulado pra preservar o valor
      // original colado em multiplas celulas.
      _pasteInProgress = true;
      return pasted;
    },
    onbeforechange: (_instance, _cell, x, y, value) => {
      // Durante drag-fill (com _fillPreview ativo), substitui o valor que o
      // Jspreadsheet ia replicar pelo numero incrementado da sequencia
      // calculada na fonte. Funciona por celula, em todas as colunas do range.
      const xi = parseInt(x);
      const yi = parseInt(y);
      const preview = _previewValueFor(xi, yi);
      if (preview != null) return preview;
      return value;
    },
    onafterchanges: (_instance, records) => {
      refreshSelectedCount();
      if (_silentMutation) return;
      // Backstop contra autosave em loop: se a mudança só tocou colunas
      // readonly/internas (MAC do monitor, status do polling, checkbox), ignora.
      // (O Jspreadsheet às vezes dispara onafterchanges async, escapando do
      // guard _silentMutation — por isso checamos as colunas afetadas aqui.)
      if (!recordsTouchEditable(records)) { _pasteInProgress = false; return; }
      if (_pasteInProgress) {
        _pasteInProgress = false;
        scheduleAutosave();
        return;
      }
      if (_fillPreview) {
        // O batch ja foi reescrito via onbeforechange — apenas finalizamos.
        _stopFillPreview();
        scheduleAutosave();
        return;
      }
      smartAutofill(records);
      scheduleAutosave();
    },
    updateTable: (_instance, cell, col, _row, val) => {
      // Decora a célula de IP com a classe de status do monitor. Roda a cada
      // render do Jspreadsheet, então as bolinhas sobrevivem a edições/refresh.
      if (col !== COL_INDEX.ip) return;
      applyPingClass(cell, String(val ?? '').trim());
    },
    oneditionstart: (_instance, cell, x, _y) => {
      if (x !== COL_INDEX.ip) return;
      // O <input> e injetado pelo Jspreadsheet apos um tick — pegar via rAF.
      requestAnimationFrame(() => {
        const input = cell.querySelector("input, textarea");
        if (!input) return;
        input.maxLength = 15;
        input.setAttribute("inputmode", "decimal");
        input.placeholder = "192.168.0.10";
        input.addEventListener("input", () => maskIPv4OnInput(input));
      });
    },
  });
  attachFillPreview(container);
  attachDevicePopover(container);
  attachPreviewClick(container);
}

// ----- Popover de ações no device vinculado (ver / desvincular) ---------------

let _devicePopover = null;

function hideDevicePopover() {
  if (_devicePopover && _devicePopover.parentNode) {
    _devicePopover.parentNode.removeChild(_devicePopover);
  }
  _devicePopover = null;
}

function showDevicePopover(td, info) {
  hideDevicePopover();
  const r = td.getBoundingClientRect();
  const pop = document.createElement('div');
  pop.className = 'ec-device-popover';
  pop.style.cssText = `
    position: fixed; left: ${r.left}px; top: ${r.bottom + 4}px;
    background: #111827; color: #e5e7eb; border: 1px solid #374151;
    border-radius: 8px; padding: 6px; z-index: 100;
    box-shadow: 0 8px 24px -8px rgba(0,0,0,0.6);
    display: flex; flex-direction: column; gap: 2px; min-width: 200px;
    font-size: 12px;
  `;
  pop.innerHTML = `
    <div style="padding: 4px 8px; font-size: 11px; color: #9ca3af;">
      Device: <span style="color: #e5e7eb;">${info.device_name || ''}</span>
    </div>
    <button data-act="ver" style="text-align: left; padding: 6px 10px; border-radius: 6px; background: transparent; color: #93c5fd; border: none; cursor: pointer;">
      Ver telefone →
    </button>
    <button data-act="desvincular" style="text-align: left; padding: 6px 10px; border-radius: 6px; background: transparent; color: #fca5a5; border: none; cursor: pointer;">
      Desvincular
    </button>`;
  document.body.appendChild(pop);
  _devicePopover = pop;
  pop.querySelector('[data-act="ver"]').addEventListener('mouseenter', (e) => e.currentTarget.style.background = '#1f2937');
  pop.querySelector('[data-act="ver"]').addEventListener('mouseleave', (e) => e.currentTarget.style.background = 'transparent');
  pop.querySelector('[data-act="desvincular"]').addEventListener('mouseenter', (e) => e.currentTarget.style.background = '#1f2937');
  pop.querySelector('[data-act="desvincular"]').addEventListener('mouseleave', (e) => e.currentTarget.style.background = 'transparent');
  pop.querySelector('[data-act="ver"]').addEventListener('click', () => {
    window.open(`/devices/${info.device_id}`, '_blank');
    hideDevicePopover();
  });
  pop.querySelector('[data-act="desvincular"]').addEventListener('click', async () => {
    if (!confirm(`Desvincular o device ${info.device_name}? O ramal continua na planilha, mas não será mais reaplicado automaticamente.`)) return;
    try {
      await api(`/api/devices/${info.device_id}/link`, { method: 'DELETE' });
      toast.success('Desvinculado');
      hideDevicePopover();
      await reload();
    } catch (e) {
      toast.error('Falha ao desvincular');
    }
  });
}

function attachDevicePopover(container) {
  container.addEventListener('click', (e) => {
    const td = e.target.closest && e.target.closest('td');
    if (!td || td.dataset.x == null) return;
    if (parseInt(td.dataset.x) !== COL_INDEX._device) return;
    const yi = parseInt(td.dataset.y);
    if (!Number.isFinite(yi)) return;
    const allData = sheet.getData();
    const row = allData[yi];
    const lineId = row && row[COL_INDEX.id];
    if (!lineId) return;
    const info = lineDeviceMap.get(String(lineId));
    if (!info) {
      hideDevicePopover();
      return;
    }
    e.stopPropagation();
    showDevicePopover(td, info);
  });
  document.addEventListener('click', (e) => {
    if (!_devicePopover) return;
    if (_devicePopover.contains(e.target)) return;
    hideDevicePopover();
  }, true);
  window.addEventListener('scroll', hideDevicePopover, true);
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
  const counts = { applied: 0, registered: 0, outdated: 0, pending: 0, error: 0 };
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
  stopMonitor();  // dados/planilha recriados — recomeça limpo se o usuário religar
  const env = await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}`);
  modeloTelefone = env.modelo_telefone || '';
  isHtek = String(modeloTelefone).toLowerCase().startsWith("htek");
  envNome = env.nome || '';
  $('#ec-title').textContent = env.nome;
  $('#ec-subtitle').textContent = `${modeloTelefone} · ${env.linhas.length} ${env.linhas.length === 1 ? 'ramal' : 'ramais'}`;
  $('#ec-config-link').href = `/extension-configurator/environments/${encodeURIComponent(envId)}/config`;
  renderStatusPills(env.linhas);
  buildSheet(env.linhas);
  refreshSelectedCount();
}

async function save({ silent = false } = {}) {
  try {
    const linhas = collectLinhasForSave();
    const env = await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}/lines`, {
      method: 'PUT', body: { linhas },
    });
    if (!silent) toast.success(`${env.linhas.length} linha(s) salvas`);
    // refresca IDs + status + device sem perder posicao da planilha (mutacoes
    // silenciosas pra nao re-disparar autosave nem smartAutofill)
    env.linhas.forEach((l, i) => {
      setCellSilent(i, "id", l.id);
      setCellSilent(i, "_preview", l.id ? "👁" : "");  // habilita o olho em linhas recém-salvas
      setCellSilent(i, "_status", statusLabel(l.status || "pending"));
      setCellSilent(i, "_device", deviceCellLabel(l));
      lineDeviceMap.set(l.id, l.device_id ? {
        device_id: l.device_id, device_name: l.device_name,
      } : null);
    });
    renderStatusPills(env.linhas);
    return env;
  } catch (e) {
    if (!silent) toast.error('Erro ao salvar: ' + e.message);
    throw e;
  }
}

function setApplyButtonsDisabled(v) {
  $('#ec-apply').disabled = v;
  const sel = $('#ec-apply-selected');
  sel.disabled = v || getSelectedIds().length === 0;
}

async function apply({ selectedIds = null } = {}) {
  stopMonitor();  // os aparelhos reiniciam ao aplicar — ping ficaria vermelho à toa
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
    toast.success(`Aplicando ${r.total} ${r.total === 1 ? 'ramal' : 'ramais'}…`);
    currentRun = r.run_id;
    startPolling();
    pollRun();
  } catch (e) {
    toast.error('Erro: ' + e.message);
    setApplyButtonsDisabled(false);
  }
}

async function pollRun() {
  if (!currentRun || pollInFlight) return;
  pollInFlight = true;
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
      stopPolling();
      toast.info(`Aplicação finalizada · ok ${sm.done || 0} · erro ${sm.error || 0}`);
      setApplyButtonsDisabled(false);
      await reload();
    }
  } catch (e) {
    if (e && e.status === 404) {
      // Run expirou da memória do servidor (prune mantém só os últimos runs,
      // ou o serviço reiniciou). O resultado por linha já está persistido —
      // encerra o acompanhamento e recarrega a planilha.
      currentRun = null;
      stopPolling();
      $('#ec-run-panel').classList.add('hidden');
      setApplyButtonsDisabled(false);
      toast.info('Acompanhamento encerrado — veja o resultado na coluna status.');
      await reload().catch(() => {});
    }
    // Demais erros (rede intermitente): silencioso — tenta no próximo tick.
  } finally {
    pollInFlight = false;
  }
}

// ----- Monitor de ping: implementação ----------------------------------------

// Aplica a classe de status (up/down/aguardando) numa <td> da coluna IP.
function applyPingClass(cell, ip) {
  cell.classList.remove('ec-ping-cell', 'ec-ping-up', 'ec-ping-down');
  if (!monitorOn || !ip) return;
  cell.classList.add('ec-ping-cell');
  const st = pingStatusByIp.get(ip);
  if (st === true) cell.classList.add('ec-ping-up');
  else if (st === false) cell.classList.add('ec-ping-down');
  // undefined => só a bolinha cinza "aguardando" (ec-ping-cell sozinha)
}

// ----- Chip "ao vivo": feedback de que os pings estão de fato rolando ---------

function showPingStatus() {
  const el = $('#ec-ping-panel');
  if (!el) return;
  el.classList.remove('hidden');
  el.classList.add('flex');
}

function hidePingStatus() {
  const el = $('#ec-ping-panel');
  if (!el) return;
  el.classList.add('hidden');
  el.classList.remove('flex');
  const bar = $('#ec-ping-progress-bar');
  if (bar) { bar.style.transition = 'none'; bar.style.width = '0%'; }
  const rounds = $('#ec-ping-rounds'); if (rounds) rounds.textContent = '#0';
  const tally = $('#ec-ping-tally'); if (tally) tally.textContent = '—';
}

// Barra que preenche de 0→100% ao longo do intervalo, dando a sensação de
// contagem regressiva até o próximo ping. Reinicia a cada agendamento.
function animateProgressBar(durationMs) {
  const bar = $('#ec-ping-progress-bar');
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
  void bar.offsetWidth;  // força reflow pra animação reiniciar
  bar.style.transition = `width ${durationMs}ms linear`;
  bar.style.width = '100%';
}

// Pisca o painel quando uma rodada acaba de chegar (confirmação visual).
function flashPingChip() {
  const el = $('#ec-ping-panel');
  if (!el) return;
  el.classList.remove('ec-ping-flash');
  void el.offsetWidth;
  el.classList.add('ec-ping-flash');
}

function updatePingChip(res) {
  let up = 0, down = 0;
  Object.values(res || {}).forEach((r) => { if (r.online) up++; else down++; });
  pingRounds += 1;
  const rounds = $('#ec-ping-rounds'); if (rounds) rounds.textContent = `#${pingRounds}`;
  const tally = $('#ec-ping-tally');
  if (tally) tally.textContent = `🟢 ${up}  🔴 ${down}`;
  flashPingChip();
}

function monitorIntervalMs() {
  let s = parseInt($('#ec-ping-interval')?.value, 10);
  if (!Number.isFinite(s)) s = 5;
  s = Math.min(3600, Math.max(2, s));  // piso de 2s p/ não martelar a rede
  return s * 1000;
}

// IPs únicos e não-vazios da planilha (a validação fina de IPv4 é no backend).
function collectMonitorIps() {
  if (!sheet) return [];
  const ipx = COL_INDEX.ip;
  const set = new Set();
  for (const row of sheet.getData()) {
    const ip = String(row[ipx] ?? '').trim();
    if (ip) set.add(ip);
  }
  return [...set];
}

// Repinta todas as células de IP visíveis a partir do mapa de status atual.
function decorateIpCells() {
  if (!sheet) return;
  const ipx = COL_INDEX.ip;
  document.querySelectorAll(`#ec-spreadsheet td[data-x="${ipx}"]`).forEach((td) => {
    const y = parseInt(td.dataset.y);
    if (!Number.isFinite(y)) return;
    applyPingClass(td, String(sheet.getValueFromCoords(ipx, y) ?? '').trim());
  });
}

// Preenche a célula MAC da linha (casada por IP) quando o ARP resolve um valor
// novo. Silencioso pra não disparar autosave/autofill; o backend já persistiu.
function applyMacToCell(ip, mac) {
  const idx = findRowIdxByIp(ip);
  if (idx < 0) return;
  const cur = String(sheet.getValueFromCoords(COL_INDEX._mac, idx) ?? '');
  if (cur !== mac) setCellSilent(idx, '_mac', mac);
}

async function pingRound() {
  if (!monitorOn || monitorBusy) return;
  const ips = collectMonitorIps();
  if (!ips.length) { decorateIpCells(); return; }
  monitorBusy = true;
  monitorAbort = new AbortController();
  try {
    const res = await api('/api/extension-configurator/ping', {
      method: 'POST', body: { ips, environment_id: envId }, signal: monitorAbort.signal,
    });
    if (!monitorOn) return;  // desligou durante o request
    Object.entries(res || {}).forEach(([ip, r]) => {
      pingStatusByIp.set(ip, !!r.online);
      if (r.mac) applyMacToCell(ip, r.mac);  // MAC coletado via ARP → preenche a coluna
    });
    decorateIpCells();
    updatePingChip(res);
  } catch (_e) {
    // abortado (desligou/saiu) ou rede instável: não quebra, tenta no próximo ciclo
  } finally {
    monitorBusy = false;
    monitorAbort = null;
  }
}

// setTimeout encadeado (não setInterval): a próxima rodada só agenda depois da
// anterior terminar, evitando empilhar requests se o ping demorar.
function scheduleNextPing() {
  if (monitorTimer) { clearTimeout(monitorTimer); monitorTimer = null; }
  if (!monitorOn) return;
  const ms = monitorIntervalMs();
  animateProgressBar(ms);  // barra preenche enquanto aguarda a próxima rodada
  monitorTimer = setTimeout(async () => {
    await pingRound();
    scheduleNextPing();
  }, ms);
}

function startMonitor() {
  if (monitorOn) return;
  monitorOn = true;
  pingRounds = 0;
  const t = $('#ec-ping-toggle'); if (t) t.checked = true;
  pingStatusByIp.clear();
  showPingStatus();
  decorateIpCells();                       // pinta tudo como "aguardando"
  pingRound().then(scheduleNextPing);      // 1ª rodada imediata, depois agenda
}

function stopMonitor() {
  monitorOn = false;
  if (monitorTimer) { clearTimeout(monitorTimer); monitorTimer = null; }
  if (monitorAbort) { monitorAbort.abort(); monitorAbort = null; }  // mata o fetch em voo
  const t = $('#ec-ping-toggle'); if (t) t.checked = false;
  pingStatusByIp.clear();
  hidePingStatus();
  decorateIpCells();                       // limpa as bolinhas
}

// ----- Preview (dry-run): mostra o que será escrito no aparelho ----------------

function getActiveRowIndex() {
  // sheet.selectedCell = [x1, y1, x2, y2] quando há célula/seleção ativa.
  const sc = sheet && sheet.selectedCell;
  if (!Array.isArray(sc) || sc.length < 2) return -1;
  const y = parseInt(sc[1]);
  return Number.isFinite(y) ? y : -1;
}

let _previewModal = null;
function closePreviewModal() {
  if (_previewModal && _previewModal.parentNode) _previewModal.parentNode.removeChild(_previewModal);
  _previewModal = null;
  document.removeEventListener('keydown', _previewEsc, true);
}
function _previewEsc(e) { if (e.key === 'Escape') closePreviewModal(); }

function showPreviewModal(data) {
  closePreviewModal();
  const meta = STATUS_META[data.status] || STATUS_META.pending;
  const mudarBadge = data.vai_mudar
    ? '<span class="px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30">vai aplicar</span>'
    : '<span class="px-2 py-0.5 rounded-full text-[11px] font-medium bg-green-500/15 text-green-400 ring-1 ring-inset ring-green-500/30">em dia</span>';
  const linhas = (data.campos || []).map((c) => `
    <tr class="border-b border-gray-800/60">
      <td class="py-1.5 pr-4 text-gray-400 align-top whitespace-nowrap">${esc(c.label)}</td>
      <td class="py-1.5 text-gray-100 font-mono break-all">${esc(c.valor)}</td>
    </tr>`).join('');

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;z-index:200;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;padding:24px;';
  overlay.innerHTML = `
    <div style="background:#111827;border:1px solid #374151;border-radius:14px;max-width:720px;width:100%;max-height:85vh;display:flex;flex-direction:column;box-shadow:0 20px 60px -20px rgba(0,0,0,0.7);">
      <div style="display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid #1f2937;">
        <div style="flex:1;min-width:0;">
          <div style="font-size:14px;font-weight:600;color:#f3f4f6;">Pré-visualização — ramal ${esc(data.numero_ramal || '—')}</div>
          <div style="font-size:11px;color:#9ca3af;margin-top:2px;">${esc(data.modelo_telefone || '')} · ${esc(data.ip || 'sem IP')}</div>
        </div>
        <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-${meta.color}-500/15 text-${meta.color}-400 ring-${meta.color}-500/30">${meta.label}</span>
        ${mudarBadge}
        <button data-act="close" style="background:transparent;border:none;color:#9ca3af;font-size:20px;cursor:pointer;line-height:1;padding:0 4px;">×</button>
      </div>
      <div style="padding:14px 18px;overflow:auto;">
        <p style="font-size:11px;color:#6b7280;margin:0 0 10px;">O que será gravado no aparelho ao aplicar (senhas mascaradas). Nada é enviado nesta tela.</p>
        <table style="width:100%;border-collapse:collapse;font-size:12px;">${linhas || '<tr><td style="color:#6b7280;padding:8px 0;">Sem campos.</td></tr>'}</table>
        <details style="margin-top:14px;">
          <summary style="cursor:pointer;color:#93c5fd;font-size:12px;">Ver XML cru</summary>
          <pre style="margin-top:8px;background:#0b1220;border:1px solid #1f2937;border-radius:8px;padding:10px;font-size:11px;color:#cbd5e1;overflow:auto;max-height:280px;white-space:pre-wrap;word-break:break-all;">${esc(data.xml || '')}</pre>
        </details>
      </div>
    </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closePreviewModal(); });
  overlay.querySelector('[data-act="close"]').addEventListener('click', closePreviewModal);
  document.body.appendChild(overlay);
  _previewModal = overlay;
  document.addEventListener('keydown', _previewEsc, true);
}

async function openPreviewForLineId(lineId) {
  if (!lineId) { toast.info('Salve a planilha antes de pré-visualizar esta linha.'); return; }
  try {
    const data = await api(
      `/api/extension-configurator/environments/${encodeURIComponent(envId)}/lines/${encodeURIComponent(lineId)}/preview`,
    );
    showPreviewModal(data);
  } catch (e) {
    toast.error('Falha ao gerar preview: ' + e.message);
  }
}

// Clique no ícone 👁 da linha → preview daquela linha (jeito intuitivo, não
// precisa pré-selecionar célula). A coluna _preview só tem 👁 em linhas salvas.
function attachPreviewClick(container) {
  container.addEventListener('click', (e) => {
    const td = e.target.closest && e.target.closest('td');
    if (!td || td.dataset.x == null) return;
    if (parseInt(td.dataset.x) !== COL_INDEX._preview) return;
    const yi = parseInt(td.dataset.y);
    if (!Number.isFinite(yi)) return;
    const row = sheet.getData()[yi];
    const lineId = row && row[COL_INDEX.id];
    if (!lineId) { toast.info('Salve a planilha antes de pré-visualizar (linha ainda sem ID).'); return; }
    e.stopPropagation();
    openPreviewForLineId(String(lineId));
  });
}

// Botão da barra: usa a linha da célula atualmente selecionada (atalho).
async function previewActiveLine() {
  const idx = getActiveRowIndex();
  if (idx < 0) { toast.info('Clique no ícone 👁 da linha que quer pré-visualizar (ou selecione uma célula da linha).'); return; }
  const row = sheet.getData()[idx];
  const lineId = row && row[COL_INDEX.id];
  await openPreviewForLineId(lineId ? String(lineId) : '');
}

// ----- Exportar ambiente (.mwrenv cifrado) -------------------------------------

function openExportModal() {
  $('#ec-export-pass').value = '';
  $('#ec-export-modal').classList.remove('hidden');
  $('#ec-export-pass').focus();
}
function closeExportModal() { $('#ec-export-modal').classList.add('hidden'); }

async function doExportEnv() {
  const passphrase = $('#ec-export-pass').value;
  if (!passphrase) { toast.error('Informe uma passphrase'); return; }
  const btn = $('#ec-export-confirm');
  btn.disabled = true;
  try {
    // O endpoint devolve o envelope JSON cifrado; baixamos como .mwrenv.
    const envelope = await api(
      `/api/extension-configurator/environments/${encodeURIComponent(envId)}/export`,
      { method: 'POST', body: { passphrase } },
    );
    const blob = new Blob([JSON.stringify(envelope)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${envId}.mwrenv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    closeExportModal();
    toast.success('Ambiente exportado (cifrado)');
  } catch (e) {
    toast.error('Falha ao exportar: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

// ----- Duplicar ambiente -------------------------------------------------------

function openDupModal() {
  $('#ec-dup-nome').value = `Cópia de ${envNome}`;
  $('#ec-dup-confirm').disabled = false;
  $('#ec-dup-modal').classList.remove('hidden');
  setTimeout(() => { $('#ec-dup-nome').focus(); $('#ec-dup-nome').select(); }, 50);
}
function closeDupModal() { $('#ec-dup-modal').classList.add('hidden'); }

async function doDuplicate() {
  const nome = $('#ec-dup-nome').value.trim();
  const btn = $('#ec-dup-confirm');
  btn.disabled = true;
  try {
    const env = await api(
      `/api/extension-configurator/environments/${encodeURIComponent(envId)}/duplicate`,
      { method: 'POST', body: { nome: nome || undefined } },
    );
    // Vai direto pro novo ambiente para cadastrar os ramais.
    location.href = `/extension-configurator/environments/${encodeURIComponent(env.id)}`;
  } catch (e) {
    toast.error('Falha ao duplicar: ' + e.message);
    btn.disabled = false;
  }
}

// --- handlers ---
// Salvar é ação na própria tela → mantém o monitor ativo (só sair da tela desliga).
$('#ec-save').addEventListener('click', save);
$('#ec-preview').addEventListener('click', previewActiveLine);
$('#ec-duplicate').addEventListener('click', openDupModal);
$('#ec-dup-cancel').addEventListener('click', closeDupModal);
$('#ec-dup-confirm').addEventListener('click', doDuplicate);
$('#ec-dup-modal').addEventListener('click', (e) => { if (e.target.id === 'ec-dup-modal') closeDupModal(); });
$('#ec-export').addEventListener('click', openExportModal);
$('#ec-export-cancel').addEventListener('click', closeExportModal);
$('#ec-export-confirm').addEventListener('click', doExportEnv);
$('#ec-export-modal').addEventListener('click', (e) => { if (e.target.id === 'ec-export-modal') closeExportModal(); });
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

$('#ec-ping-toggle').addEventListener('change', (e) => {
  if (e.target.checked) startMonitor(); else stopMonitor();
});
$('#ec-ping-interval').addEventListener('change', () => {
  if (monitorOn) scheduleNextPing();  // aplica o novo intervalo na hora
});

// Sair da tela (navegar/fechar aba) encerra o monitor e o polling do run.
window.addEventListener('pagehide', () => { stopMonitor(); stopPolling(); });

reload().catch((e) => toast.error('Falha ao carregar: ' + e.message));
