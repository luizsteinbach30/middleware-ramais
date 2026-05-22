import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function envCard(e) {
  return `
    <div class="group relative bg-gray-800 hover:bg-gray-800/80 ring-1 ring-gray-700 rounded-xl transition-colors">
      <a href="/extension-configurator/environments/${encodeURIComponent(e.id)}"
         class="block p-4 pr-10">
        <div class="flex items-baseline justify-between">
          <h3 class="text-sm font-semibold text-gray-100 truncate">${esc(e.nome)}</h3>
          <span class="text-xs text-gray-500 ml-2 flex-shrink-0">${e.telefones} Ramais</span>
        </div>
        <p class="text-xs text-gray-400 mt-1">${esc(e.modelo_telefone)}</p>
        <p class="text-[10px] text-gray-500 mt-2">Atualizado: ${esc(e.atualizado_em || '—')}</p>
      </a>
      <button
        data-action="delete"
        data-id="${esc(e.id)}"
        data-nome="${esc(e.nome)}"
        data-telefones="${e.telefones}"
        title="Apagar ambiente"
        class="absolute top-2.5 right-2.5 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity p-1.5 rounded-md text-gray-500 hover:text-red-300 hover:bg-red-500/15 ring-1 ring-transparent hover:ring-red-500/30">
        <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M8.75 1A2.75 2.75 0 006 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 10.23 1.482l.149-.022.841 10.518A2.75 2.75 0 007.596 19h4.807a2.75 2.75 0 002.742-2.53l.841-10.52.149.023a.75.75 0 00.23-1.482A41.03 41.03 0 0014 4.193V3.75A2.75 2.75 0 0011.25 1h-2.5zM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4zM8.58 7.72a.75.75 0 00-1.5.06l.3 7.5a.75.75 0 101.5-.06l-.3-7.5zm4.34.06a.75.75 0 10-1.5-.06l-.3 7.5a.75.75 0 101.5.06l.3-7.5z" clip-rule="evenodd"/>
        </svg>
      </button>
    </div>`;
}

async function load() {
  const [{ environments }, { models }] = await Promise.all([
    api('/api/extension-configurator/environments'),
    api('/api/extension-configurator/phone-models'),
  ]);
  $('#ec-count').textContent = `${environments.length} ambiente${environments.length === 1 ? '' : 's'}`;
  $('#ec-grid').innerHTML = environments.map(envCard).join('');
  $('#ec-empty').classList.toggle('hidden', environments.length > 0);
  const sel = $('#ec-new-modelo');
  sel.innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
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
    // Recem-criado: vai direto pra config padrao
    location.href = `/extension-configurator/environments/${encodeURIComponent(env.id)}/config`;
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

// Delegacao do clique no botao "apagar" de cada card
$('#ec-grid').addEventListener('click', (e) => {
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

load().catch((e) => toast.error('Falha ao carregar: ' + e.message));
