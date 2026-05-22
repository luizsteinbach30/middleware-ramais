import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function envCard(e) {
  return `
    <a href="/extension-configurator/environments/${encodeURIComponent(e.id)}"
       class="block bg-gray-800 hover:bg-gray-800/80 ring-1 ring-gray-700 rounded-xl p-4 transition-colors">
      <div class="flex items-baseline justify-between">
        <h3 class="text-sm font-semibold text-gray-100 truncate">${esc(e.nome)}</h3>
        <span class="text-xs text-gray-500">${e.telefones} ramal${e.telefones === 1 ? '' : 'is'}</span>
      </div>
      <p class="text-xs text-gray-400 mt-1">${esc(e.modelo_telefone)}</p>
      <p class="text-[10px] text-gray-500 mt-2">Atualizado: ${esc(e.atualizado_em || '—')}</p>
    </a>`;
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
    // Ambiente recem-criado: leva direto pra config padrao para o usuario
    // ajustar credencial, function keys e validacao antes de mexer na planilha.
    location.href = `/extension-configurator/environments/${encodeURIComponent(env.id)}/config`;
  } catch (err) {
    toast.error('Erro: ' + err.message);
  }
});

load().catch((e) => toast.error('Falha ao carregar: ' + e.message));
