import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

const STATUS_PILL = {
  ok:        { color: "green",  label: "todos aplicados" },
  pendentes: { color: "yellow", label: "pendentes" },
  erros:     { color: "red",    label: "erros" },
  vazio:     { color: "gray",   label: "sem ramais" },
};

// Ícones da barra de ações do card (canto superior direito, no hover).
const ICON_CHECK = '<svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.71-9.96a.75.75 0 00-1.06-1.06l-3.4 3.39-1.24-1.24a.75.75 0 10-1.06 1.06l1.77 1.77c.3.3.77.3 1.06 0l3.93-3.92z" clip-rule="evenodd"/></svg>';
const ICON_CIRCLE = '<svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 2a8 8 0 100 16 8 8 0 000-16zm0 1.5a6.5 6.5 0 110 13 6.5 6.5 0 010-13z" clip-rule="evenodd"/></svg>';
const ICON_DUPLICATE = '<svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path d="M7 3.5A1.5 1.5 0 018.5 2h3.879a1.5 1.5 0 011.06.44l3.122 3.12A1.5 1.5 0 0117 6.622V12.5a1.5 1.5 0 01-1.5 1.5h-1v-3.379a3 3 0 00-.879-2.121L10.5 5.379A3 3 0 008.379 4.5H7v-1z"/><path d="M4.5 6A1.5 1.5 0 003 7.5v9A1.5 1.5 0 004.5 18h7a1.5 1.5 0 001.5-1.5v-5.879a1.5 1.5 0 00-.44-1.06L9.44 6.439A1.5 1.5 0 008.378 6H4.5z"/></svg>';
const ICON_TRASH = '<svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193V3.75A2.75 2.75 0 0011.25 1h-2.5zM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4zM8.58 7.72a.75.75 0 00-1.5.06l.3 7.5a.75.75 0 101.5-.06l-.3-7.5zm4.34.06a.75.75 0 10-1.5-.06l-.3 7.5a.75.75 0 101.5.06l.3-7.5z" clip-rule="evenodd"/></svg>';

const FILTER_KEY = "ec.list.filters.v1";

// Cache em memoria dos envs vindos do backend (filtrados client-side).
let _allEnvs = [];
let _filters = { q: "", modelo: "", status: "" };
// Ambientes marcados para exportação (persistem entre re-renders do grid).
const selectedEnvs = new Set();

function loadFilters() {
  try {
    const raw = localStorage.getItem(FILTER_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    _filters = { q: "", modelo: "", status: "", ...parsed };
  } catch (_e) { /* ignore */ }
}

function saveFilters() {
  try { localStorage.setItem(FILTER_KEY, JSON.stringify(_filters)); } catch (_e) { /* ignore */ }
}

function statusPill(meta) {
  const p = STATUS_PILL[meta?.agregado] || STATUS_PILL.vazio;
  // total de ramais com problema p/ ajudar diagnostico rapido
  let badge = "";
  if (meta?.error) badge = ` (${meta.error})`;
  else if (meta?.agregado === "pendentes" && meta?.pending) badge = ` (${meta.pending})`;
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium ring-1 ring-inset bg-${p.color}-500/15 text-${p.color}-400 ring-${p.color}-500/30">
    <span class="w-1.5 h-1.5 rounded-full bg-${p.color}-400"></span>${p.label}${badge}
  </span>`;
}

function envCard(e) {
  const vincPct = e.telefones > 0
    ? Math.round((e.devices_vinculados / e.telefones) * 100)
    : 0;
  const vincTone = vincPct >= 100 ? "green" : vincPct >= 50 ? "blue" : vincPct > 0 ? "yellow" : "gray";
  const vincLabel = e.devices_vinculados > 0
    ? `${e.devices_vinculados}/${e.telefones} devices vinculados`
    : `nenhum device vinculado`;
  const ramaisLabel = `${e.telefones} ${e.telefones === 1 ? 'ramal' : 'ramais'}`;
  const isSel = selectedEnvs.has(e.id);
  // Realce do card selecionado: borda azul + fundo levemente azul + leve elevação.
  const shell = isSel
    ? 'ring-2 ring-blue-500/70 bg-blue-500/10 shadow-lg shadow-blue-500/20 -translate-y-0.5'
    : 'ring-1 ring-gray-700 bg-gray-800 hover:bg-gray-800/80';
  // Botão "selecionar": fica visível quando selecionado; senão aparece no hover.
  const selBtn = isSel
    ? 'opacity-100 text-blue-300 bg-blue-500/20 ring-blue-500/40'
    : 'opacity-0 group-hover:opacity-100 text-gray-500 hover:text-blue-300 hover:bg-blue-500/15 ring-transparent hover:ring-blue-500/30';
  return `
    <div data-card="${esc(e.id)}" class="group relative rounded-xl transition-all duration-150 ${shell}">
      <a href="/extension-configurator/environments/${encodeURIComponent(e.id)}"
         class="block p-4">
        <h3 class="text-sm font-semibold text-gray-100 truncate pr-24">${esc(e.nome)}</h3>
        <p class="text-xs text-gray-400 mt-1 truncate pr-2">${esc(e.modelo_telefone)} · ${ramaisLabel}</p>
        <div class="mt-2 flex items-center gap-2 flex-wrap">
          ${statusPill(e.status_resumo)}
          <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium ring-1 ring-inset bg-${vincTone}-500/15 text-${vincTone}-400 ring-${vincTone}-500/30">
            <span class="w-1.5 h-1.5 rounded-full bg-${vincTone}-400"></span>${vincLabel}
          </span>
        </div>
        <div class="mt-1.5 text-[10px] text-gray-500 truncate">Atualizado: ${e.atualizado_em ? esc(fmtTs(e.atualizado_em)) : '—'}</div>
      </a>
      <div class="absolute top-2.5 right-2.5 flex items-center gap-1">
        <button data-action="select" data-id="${esc(e.id)}"
          title="${isSel ? 'Selecionado — clique para desmarcar' : 'Selecionar (exportar)'}"
          class="transition-all p-1.5 rounded-md ring-1 ${selBtn}">${isSel ? ICON_CHECK : ICON_CIRCLE}</button>
        <button data-action="duplicate" data-id="${esc(e.id)}" data-nome="${esc(e.nome)}"
          title="Duplicar ambiente"
          class="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1.5 rounded-md text-gray-500 hover:text-blue-300 hover:bg-blue-500/15 ring-1 ring-transparent hover:ring-blue-500/30">${ICON_DUPLICATE}</button>
        <button data-action="delete" data-id="${esc(e.id)}" data-nome="${esc(e.nome)}" data-telefones="${e.telefones}"
          title="Apagar ambiente"
          class="opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1.5 rounded-md text-gray-500 hover:text-red-300 hover:bg-red-500/15 ring-1 ring-transparent hover:ring-red-500/30">${ICON_TRASH}</button>
      </div>
    </div>`;
}

// Match: termos separados por espaco viram AND; cada termo precisa aparecer
// em searchable (= nome do ambiente, lowercase, vindo do backend). Dados
// internos da planilha (ramal/IP/MAC/user auth) ficam fora da busca livre.
function matchesFilters(env) {
  if (_filters.modelo && env.modelo_telefone !== _filters.modelo) return false;
  if (_filters.status && env.status_resumo?.agregado !== _filters.status) return false;
  const q = _filters.q.trim().toLowerCase();
  if (!q) return true;
  const haystack = env.searchable || String(env.nome || '').toLowerCase();
  return q.split(/\s+/).every(t => t && haystack.includes(t));
}

function renderGrid() {
  const total = _allEnvs.length;
  const visible = _allEnvs.filter(matchesFilters);
  const hasFilters = !!(_filters.q || _filters.modelo || _filters.status);

  $('#ec-grid').innerHTML = visible.map(envCard).join('');
  $('#ec-empty').classList.toggle('hidden', total > 0);
  $('#ec-no-match').classList.toggle('hidden', total === 0 || visible.length > 0);

  const counter = total === 0
    ? "Nenhum ambiente"
    : (hasFilters
        ? `${visible.length} de ${total} ambiente${total === 1 ? '' : 's'}`
        : `${total} ambiente${total === 1 ? '' : 's'}`);
  $('#ec-count').textContent = counter;
  updateSelInfo();
}

function updateSelInfo() {
  const n = selectedEnvs.size;
  const el = $('#ec-sel-info');
  if (el) el.textContent = n ? `${n} selecionado${n === 1 ? '' : 's'}` : 'exporta os visíveis';
  // "Apagar selecionados" só existe com seleção ativa (ação destrutiva).
  const del = $('#ec-del-selected');
  if (del) {
    del.classList.toggle('hidden', n === 0);
    del.classList.toggle('inline-flex', n > 0);
  }
}

function exportTargets() {
  if (selectedEnvs.size) return [...selectedEnvs];
  return _allEnvs.filter(matchesFilters).map((e) => e.id);
}

function doExport(fmt) {
  const ids = exportTargets();
  if (!ids.length) { toast.error('Nenhum ambiente para exportar'); return; }
  const url = `/api/extension-configurator/export?format=${fmt}&ids=${encodeURIComponent(ids.join(','))}`;
  const a = document.createElement('a');
  a.href = url;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function syncFilterInputsFromState() {
  $('#ec-filter-q').value = _filters.q;
  $('#ec-filter-modelo').value = _filters.modelo;
  $('#ec-filter-status').value = _filters.status;
}

function populateModeloFilter(models) {
  const sel = $('#ec-filter-modelo');
  const cur = _filters.modelo;
  // Mostra os modelos que efetivamente existem entre os ambientes carregados,
  // mais qualquer um do catalogo que estiver em uso.
  const inUse = new Set(_allEnvs.map(e => e.modelo_telefone).filter(Boolean));
  const options = ['<option value="">Todos os modelos</option>'];
  for (const m of models) {
    const used = inUse.has(m);
    const label = used ? m : `${m} (não usado)`;
    options.push(`<option value="${esc(m)}"${used ? '' : ' disabled'}>${esc(label)}</option>`);
  }
  sel.innerHTML = options.join('');
  sel.value = cur;
}

async function load() {
  const [{ environments }, { models }] = await Promise.all([
    api('/api/extension-configurator/environments'),
    api('/api/extension-configurator/phone-models'),
  ]);
  _allEnvs = environments;
  populateModeloFilter(models);
  const newSel = $('#ec-new-modelo');
  newSel.innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
  renderGrid();
}

// --- modal: criar ---
function openModal() {
  $('#ec-new-nome').value = '';
  $('#ec-modal').classList.remove('hidden');
  $('#ec-new-nome').focus();
}
function closeModal() { $('#ec-modal').classList.add('hidden'); }

$('#ec-new').addEventListener('click', openModal);
$('#ec-modal-cancel').addEventListener('click', closeModal);
$('#ec-modal').addEventListener('click', (e) => { if (e.target.id === 'ec-modal') closeModal(); });
$('#ec-modal-create').addEventListener('click', async () => {
  const nome = $('#ec-new-nome').value.trim();
  const modelo = $('#ec-new-modelo').value;
  if (!nome) { toast.error('Nome obrigatório'); return; }
  try {
    const env = await api('/api/extension-configurator/environments', {
      method: 'POST', body: { nome, modelo_telefone: modelo },
    });
    closeModal();
    // Recém-criado: config padrão primeiro; `novo=1` faz o Salvar de lá seguir
    // direto para a planilha de ramais.
    location.href =
      `/extension-configurator/environments/${encodeURIComponent(env.id)}/config?novo=1`;
  } catch (err) {
    toast.error('Erro: ' + err.message);
  }
});

// --- modal: apagar ---
let pendingDelete = null;  // { id, nome, telefones }
const delModal = $('#ec-del-modal');
const delConfirmInput = $('#ec-del-confirm');
const delConfirmBtn = $('#ec-del-confirm-btn');

function openDeleteModal(meta) {
  pendingDelete = meta;
  $('#ec-del-name').textContent = meta.nome;
  const ramais = Number(meta.telefones) || 0;
  $('#ec-del-info').textContent =
    ramais > 0
      ? `${ramais} ${ramais === 1 ? 'ramal' : 'ramais'} e todo o histórico de execuções`
      : 'todo o histórico de execuções';
  $('#ec-del-confirm-label').textContent = meta.nome;
  delConfirmInput.value = '';
  delConfirmBtn.disabled = true;
  delModal.classList.remove('hidden');
  setTimeout(() => delConfirmInput.focus(), 50);
}
function closeDeleteModal() {
  delModal.classList.add('hidden');
  pendingDelete = null;
}

delConfirmInput.addEventListener('input', () => {
  delConfirmBtn.disabled = !pendingDelete || delConfirmInput.value.trim() !== pendingDelete.nome;
});
$('#ec-del-cancel').addEventListener('click', closeDeleteModal);
delModal.addEventListener('click', (e) => { if (e.target === delModal) closeDeleteModal(); });

delConfirmBtn.addEventListener('click', async () => {
  if (!pendingDelete) return;
  const { id, nome } = pendingDelete;
  delConfirmBtn.disabled = true;
  try {
    await api(`/api/extension-configurator/environments/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
    closeDeleteModal();
    toast.success(`Ambiente "${nome}" apagado`);
    await load();
  } catch (err) {
    toast.error('Erro ao apagar: ' + err.message);
    delConfirmBtn.disabled = false;
  }
});

// --- modal: apagar selecionados (em massa) ---
// Reusa o DELETE por ambiente (não há endpoint de bulk no backend): dispara em
// série para não abrir N transações concorrentes no SQLite, e reporta parciais
// — se 2 de 5 falharem, o usuário vê quais.
const delSelModal = $('#ec-delsel-modal');
const delSelInput = $('#ec-delsel-confirm');
const delSelBtn = $('#ec-delsel-confirm-btn');
const DELSEL_PALAVRA = 'APAGAR';

function selectedEnvObjects() {
  return _allEnvs.filter((e) => selectedEnvs.has(e.id));
}

function openDeleteSelectedModal() {
  const alvos = selectedEnvObjects();
  if (!alvos.length) return;
  const totalRamais = alvos.reduce((acc, e) => acc + (Number(e.telefones) || 0), 0);
  $('#ec-delsel-count').textContent =
    `${alvos.length} ambiente${alvos.length === 1 ? '' : 's'}` +
    (totalRamais ? ` (${totalRamais} ${totalRamais === 1 ? 'ramal' : 'ramais'})` : '');
  $('#ec-delsel-list').innerHTML = alvos.map((e) => {
    const n = Number(e.telefones) || 0;
    return `<li class="flex items-center justify-between gap-2">
      <span class="truncate">${esc(e.nome)}</span>
      <span class="text-gray-500 shrink-0">${n} ${n === 1 ? 'ramal' : 'ramais'}</span>
    </li>`;
  }).join('');
  delSelInput.value = '';
  delSelBtn.disabled = true;
  delSelModal.classList.remove('hidden');
  setTimeout(() => delSelInput.focus(), 50);
}

function closeDeleteSelectedModal() { delSelModal.classList.add('hidden'); }

delSelInput.addEventListener('input', () => {
  delSelBtn.disabled = delSelInput.value.trim().toUpperCase() !== DELSEL_PALAVRA;
});
$('#ec-del-selected').addEventListener('click', openDeleteSelectedModal);
$('#ec-delsel-cancel').addEventListener('click', closeDeleteSelectedModal);
delSelModal.addEventListener('click', (e) => {
  if (e.target === delSelModal) closeDeleteSelectedModal();
});

delSelBtn.addEventListener('click', async () => {
  const alvos = selectedEnvObjects();
  if (!alvos.length) return;
  delSelBtn.disabled = true;
  const falhas = [];
  for (const env of alvos) {
    try {
      await api(`/api/extension-configurator/environments/${encodeURIComponent(env.id)}`, {
        method: 'DELETE',
      });
      selectedEnvs.delete(env.id);
    } catch (err) {
      falhas.push(`${env.nome} (${err.message})`);
    }
  }
  closeDeleteSelectedModal();
  const apagados = alvos.length - falhas.length;
  if (apagados) {
    toast.success(`${apagados} ambiente${apagados === 1 ? '' : 's'} apagado${apagados === 1 ? '' : 's'}`);
  }
  if (falhas.length) {
    toast.error(`Falha em ${falhas.length}: ${falhas.slice(0, 3).join(' · ')}`);
  }
  await load();
});

// --- modal: duplicar ---
let pendingDup = null;  // { id, nome }
const dupModal = $('#ec-dup-modal');
const dupConfirmBtn = $('#ec-dup-confirm');

function openDupModal(meta) {
  pendingDup = meta;
  $('#ec-dup-src').textContent = meta.nome;
  $('#ec-dup-nome').value = `Cópia de ${meta.nome}`;
  dupConfirmBtn.disabled = false;
  dupModal.classList.remove('hidden');
  setTimeout(() => { $('#ec-dup-nome').focus(); $('#ec-dup-nome').select(); }, 50);
}
function closeDupModal() { dupModal.classList.add('hidden'); pendingDup = null; }

$('#ec-dup-cancel').addEventListener('click', closeDupModal);
dupModal.addEventListener('click', (e) => { if (e.target === dupModal) closeDupModal(); });

dupConfirmBtn.addEventListener('click', async () => {
  if (!pendingDup) return;
  const nome = $('#ec-dup-nome').value.trim();
  dupConfirmBtn.disabled = true;
  try {
    const env = await api(
      `/api/extension-configurator/environments/${encodeURIComponent(pendingDup.id)}/duplicate`,
      { method: 'POST', body: { nome: nome || undefined } },
    );
    closeDupModal();
    // Clonar também passa pela config padrão (é onde se revisa credencial e
    // teclas do novo ambiente); ao salvar lá, segue para a planilha.
    location.href =
      `/extension-configurator/environments/${encodeURIComponent(env.id)}/config?novo=1`;
  } catch (err) {
    toast.error('Falha ao duplicar: ' + err.message);
    dupConfirmBtn.disabled = false;
  }
});

// Delegacao do clique na barra de acoes de cada card (selecionar / duplicar /
// apagar). Clicar no corpo do card continua abrindo o ambiente (link <a>).
$('#ec-grid').addEventListener('click', (e) => {
  const sel = e.target.closest('button[data-action="select"]');
  if (sel) {
    e.preventDefault();
    e.stopPropagation();
    const id = sel.dataset.id;
    if (selectedEnvs.has(id)) selectedEnvs.delete(id); else selectedEnvs.add(id);
    renderGrid();  // re-renderiza p/ aplicar o realce do card selecionado
    return;
  }
  const dup = e.target.closest('button[data-action="duplicate"]');
  if (dup) {
    e.preventDefault();
    e.stopPropagation();
    openDupModal({ id: dup.dataset.id, nome: dup.dataset.nome });
    return;
  }
  const btn = e.target.closest('button[data-action="delete"]');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  openDeleteModal({
    id: btn.dataset.id,
    nome: btn.dataset.nome,
    telefones: btn.dataset.telefones,
  });
});

$('#ec-export-xlsx').addEventListener('click', () => doExport('xlsx'));
$('#ec-export-pdf').addEventListener('click', () => doExport('pdf'));

// --- Exportar backup dos selecionados (.mwrbak cifrado) ---
// Mesma regra de alvo do XLSX/PDF: com seleção, exporta o que está marcado;
// sem seleção, o que está visível pelos filtros. O que muda é o conteúdo — aqui
// vão as senhas de verdade, para o ambiente funcionar no destino.
const bakModal = $('#ec-bak-modal');

function openBakModal() {
  const ids = exportTargets();
  if (!ids.length) { toast.error('Nenhum ambiente para exportar'); return; }
  const nomes = _allEnvs.filter((e) => ids.includes(e.id)).map((e) => e.nome);
  $('#ec-bak-alvo').textContent = nomes.length === 1
    ? `Ambiente: ${nomes[0]}`
    : `${nomes.length} ambientes: ${nomes.slice(0, 4).join(', ')}${nomes.length > 4 ? '…' : ''}`;
  $('#ec-bak-pass').value = '';
  bakModal.classList.remove('hidden');
  $('#ec-bak-pass').focus();
}
function closeBakModal() { bakModal.classList.add('hidden'); }

$('#ec-export-bak').addEventListener('click', openBakModal);
$('#ec-bak-cancel').addEventListener('click', closeBakModal);
bakModal.addEventListener('click', (e) => { if (e.target === bakModal) closeBakModal(); });

$('#ec-bak-confirm').addEventListener('click', async () => {
  const ids = exportTargets();
  const passphrase = $('#ec-bak-pass').value;
  if (!passphrase) { toast.error('Informe uma passphrase'); return; }
  const btn = $('#ec-bak-confirm');
  btn.disabled = true;
  try {
    const envelope = await api('/api/backup/export', {
      method: 'POST',
      body: { passphrase, sections: ['environments'], environment_ids: ids },
    });
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
    const nome = ids.length === 1 ? `${ids[0]}-${stamp}.mwrbak` : `ambientes-${stamp}.mwrbak`;
    const url = URL.createObjectURL(new Blob([JSON.stringify(envelope)], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = nome;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    closeBakModal();
    toast.success(`${ids.length} ambiente(s) exportado(s) (cifrado)`);
  } catch (err) {
    toast.error('Falha ao exportar: ' + err.message);
  } finally {
    btn.disabled = false;
  }
});

// --- Importar ambiente (.mwrenv cifrado) ---
const importModal = $('#ec-import-modal');
function openImportModal() {
  $('#ec-import-file').value = '';
  $('#ec-import-pass').value = '';
  $('#ec-import-nome').value = '';
  importModal.classList.remove('hidden');
}
function closeImportModal() { importModal.classList.add('hidden'); }

$('#ec-import').addEventListener('click', openImportModal);
$('#ec-import-cancel').addEventListener('click', closeImportModal);
importModal.addEventListener('click', (e) => { if (e.target === importModal) closeImportModal(); });

$('#ec-import-confirm').addEventListener('click', async () => {
  const file = $('#ec-import-file').files[0];
  const passphrase = $('#ec-import-pass').value;
  const nome = $('#ec-import-nome').value.trim();
  if (!file) { toast.error('Selecione um arquivo .mwrenv'); return; }
  if (!passphrase) { toast.error('Informe a passphrase'); return; }
  // Pacote de backup entra por outro caminho: lá o operador vê a comparação e
  // decide cada conflito. Aqui, esta tela só sabe criar ambiente novo — melhor
  // apontar o caminho certo do que falhar com "schema não suportado".
  if (file.name.toLowerCase().endsWith('.mwrbak')) {
    toast.error('Arquivo de backup (.mwrbak): importe em Sistema → Backup, que mostra o que muda antes de aplicar');
    return;
  }
  const btn = $('#ec-import-confirm');
  btn.disabled = true;
  try {
    const blob = await file.text();
    const r = await api('/api/extension-configurator/environments/import', {
      method: 'POST', body: { passphrase, blob, nome: nome || undefined },
    });
    closeImportModal();
    toast.success(`Ambiente "${r.nome}" importado (${r.linhas} linha(s))`);
    await load();
  } catch (err) {
    toast.error('Falha ao importar: ' + err.message);
  } finally {
    btn.disabled = false;
  }
});

// --- filtros ---
let _filterDebounce = null;
function onFilterChange() {
  _filters = {
    q: $('#ec-filter-q').value,
    modelo: $('#ec-filter-modelo').value,
    status: $('#ec-filter-status').value,
  };
  saveFilters();
  renderGrid();
}
$('#ec-filter-q').addEventListener('input', () => {
  if (_filterDebounce) clearTimeout(_filterDebounce);
  _filterDebounce = setTimeout(onFilterChange, 150);
});
$('#ec-filter-modelo').addEventListener('change', onFilterChange);
$('#ec-filter-status').addEventListener('change', onFilterChange);
$('#ec-filter-clear').addEventListener('click', () => {
  _filters = { q: "", modelo: "", status: "" };
  syncFilterInputsFromState();
  saveFilters();
  renderGrid();
  $('#ec-filter-q').focus();
});

// Restaura filtros salvos antes do primeiro render.
loadFilters();
syncFilterInputsFromState();

load().catch((e) => toast.error('Falha ao carregar: ' + e.message));
