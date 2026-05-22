import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const $ = (s) => document.querySelector(s);
const envId = window.EC_ENV_ID;

const FIELDS_STR = [
  'web_user', 'web_password', 'nova_web_user', 'nova_web_password',
  'menu_password', 'keylock_password',
];
const FIELDS_INT = ['register_expiration', 'keylock_enable', 'keylock_timeout'];
const FIELDS_BOOL = ['validar_conectividade'];

const LINEKEYS = ['LineKey1', 'LineKey2', 'LineKey3', 'LineKey4'];
const TIPOS_FUNCAO = [
  ['disabled', 'Desabilitada'],
  ['line', 'Linha SIP'],
  ['speed_dial', 'Discagem rápida (número abreviado)'],
  ['blf', 'BLF (monitorar ramal)'],
];
const VALUE_FIELDS = [
  { value: 'numero_abreviado', label: 'Número abreviado' },
  { value: 'numero_ramal',     label: 'Número do ramal' },
];

let isHtek = false;
let isIntelbras = false;

function escAttr(s) {
  return String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

function fkRow(fk) {
  const wrap = document.createElement('div');
  wrap.className = 'ec-fk-row bg-gray-900/60 ring-1 ring-inset ring-gray-700 rounded-lg p-3 grid md:grid-cols-6 gap-2 text-xs items-end';
  const account = (fk.account != null) ? fk.account : (isHtek ? 0 : 1);
  wrap.innerHTML = `
    <label class="flex flex-col gap-1">
      <span class="text-gray-400">Tecla</span>
      <select data-f="key" class="bg-gray-900 ring-1 ring-inset ring-gray-700 rounded px-2 py-1.5 text-gray-100">
        ${LINEKEYS.map(k => `<option value="${k}"${k === fk.key ? ' selected' : ''}>${k}</option>`).join('')}
      </select>
    </label>
    <label class="flex flex-col gap-1">
      <span class="text-gray-400">Tipo</span>
      <select data-f="type" class="bg-gray-900 ring-1 ring-inset ring-gray-700 rounded px-2 py-1.5 text-gray-100">
        ${TIPOS_FUNCAO.map(([v, l]) => `<option value="${v}"${v === fk.type ? ' selected' : ''}>${l}</option>`).join('')}
      </select>
    </label>
    <label class="flex flex-col gap-1">
      <span class="text-gray-400">Label</span>
      <input data-f="label" value="${escAttr(fk.label || '')}" class="bg-gray-900 ring-1 ring-inset ring-gray-700 rounded px-2 py-1.5 text-gray-100"/>
    </label>
    <label class="flex flex-col gap-1${isHtek ? ' hidden' : ''}">
      <span class="text-gray-400">Account</span>
      <input data-f="account" type="number" min="0" max="255" value="${account}" class="bg-gray-900 ring-1 ring-inset ring-gray-700 rounded px-2 py-1.5 text-gray-100"/>
    </label>
    <label class="flex flex-col gap-1 md:col-span-2">
      <span class="text-gray-400 flex items-center gap-2">Valor
        <select data-f="value_source" class="bg-gray-900 ring-1 ring-inset ring-gray-700 rounded px-1.5 py-0.5 text-[10px]">
          <option value="linha"${fk.value_source === 'linha' ? ' selected' : ''}>vem da planilha</option>
          <option value="fixo"${fk.value_source === 'fixo' ? ' selected' : ''}>fixo</option>
        </select>
      </span>
      <div class="flex gap-2">
        <select data-f="value_field" class="bg-gray-900 ring-1 ring-inset ring-gray-700 rounded px-2 py-1.5 text-gray-100 flex-1">
          ${VALUE_FIELDS.map(o => `<option value="${o.value}"${o.value === fk.value_field ? ' selected' : ''}>${o.label}</option>`).join('')}
        </select>
        <input data-f="value_fixed" value="${escAttr(fk.value_fixed || '')}" placeholder="valor fixo"
               class="bg-gray-900 ring-1 ring-inset ring-gray-700 rounded px-2 py-1.5 text-gray-100 flex-1"/>
        <button type="button" data-f="remove" class="text-red-400 hover:text-red-300 px-2">✕</button>
      </div>
    </label>
  `;
  const refresh = () => {
    const src = wrap.querySelector('[data-f="value_source"]').value;
    wrap.querySelector('[data-f="value_field"]').classList.toggle('hidden', src !== 'linha');
    wrap.querySelector('[data-f="value_fixed"]').classList.toggle('hidden', src !== 'fixo');
  };
  wrap.querySelector('[data-f="value_source"]').addEventListener('change', refresh);
  wrap.querySelector('[data-f="remove"]').addEventListener('click', () => wrap.remove());
  refresh();
  return wrap;
}

function collectFKs() {
  const list = $('#ec-fk-list');
  return Array.from(list.querySelectorAll('.ec-fk-row')).map(row => {
    const o = {};
    row.querySelectorAll('[data-f]').forEach(el => {
      if (el.tagName === 'BUTTON') return;
      const f = el.dataset.f;
      if (f === 'account') o[f] = parseInt(el.value || '0', 10);
      else o[f] = el.value;
    });
    // HTEK: softkey usa sempre Account1 (account=0). Outros valores apontam
    // para um perfil inexistente e a tecla nao disca.
    if (isHtek) o.account = 0;
    return o;
  });
}

function defaultFK() {
  return {
    key: 'LineKey3',
    type: 'speed_dial',
    label: '',
    value_source: 'linha',
    value_field: 'numero_abreviado',
    value_fixed: '',
    account: isHtek ? 0 : 1,
  };
}

async function reload() {
  const env = await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}`);
  const modelo = env.modelo_telefone || '';
  isHtek = String(modelo).toLowerCase().startsWith('htek');
  isIntelbras = String(modelo).toLowerCase().startsWith('intelbras');
  $('#ec-title').textContent = `Config padrão — ${env.nome}`;
  $('#ec-subtitle').textContent = `Modelo: ${modelo} · servidor SIP é definido por linha na planilha.`;
  $('#ec-back').href = `/extension-configurator/environments/${encodeURIComponent(envId)}`;
  const lbl = document.getElementById('ec-back-label');
  if (lbl) lbl.textContent = env.nome || 'Voltar';
  // Mostra "Function Keys" para HTEK, "DSS Keys" para Intelbras
  $('#ec-fk-legend').textContent = isIntelbras
    ? 'DSS Keys (teclas programáveis)'
    : 'Function Keys (teclas programáveis)';
  // Avancadas so para Intelbras
  $('#ec-section-advanced').classList.toggle('hidden', !isIntelbras);

  const cfg = env.config_padrao || {};
  FIELDS_STR.forEach((k) => { const el = $('#cfg-' + k); if (el) el.value = cfg[k] ?? ''; });
  FIELDS_INT.forEach((k) => { const el = $('#cfg-' + k); if (el) el.value = String(cfg[k] ?? ''); });
  FIELDS_BOOL.forEach((k) => { const el = $('#cfg-' + k); if (el) el.checked = !!cfg[k]; });

  const list = $('#ec-fk-list');
  list.innerHTML = '';
  const fks = Array.isArray(cfg.function_keys) ? cfg.function_keys : [];
  (fks.length ? fks : [defaultFK()]).forEach((fk) => list.appendChild(fkRow(fk)));
}

async function save() {
  const body = { config_padrao: {} };
  FIELDS_STR.forEach((k) => { body.config_padrao[k] = $('#cfg-' + k).value; });
  FIELDS_INT.forEach((k) => { body.config_padrao[k] = parseInt($('#cfg-' + k).value || '0', 10); });
  FIELDS_BOOL.forEach((k) => { body.config_padrao[k] = $('#cfg-' + k).checked; });
  body.config_padrao.function_keys = collectFKs();
  try {
    await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}`, {
      method: 'PUT', body,
    });
    toast.success('Configuração salva');
  } catch (e) {
    toast.error('Erro: ' + e.message);
  }
}

$('#ec-save').addEventListener('click', save);
$('#ec-fk-add').addEventListener('click', () => {
  $('#ec-fk-list').appendChild(fkRow(defaultFK()));
});

reload().catch((e) => toast.error('Falha ao carregar: ' + e.message));
