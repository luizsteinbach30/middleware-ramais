import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const $ = (s) => document.querySelector(s);
const envId = window.EC_ENV_ID;

// Ambiente recém-criado (do zero ou clonado) cai aqui primeiro: ao salvar, o
// fluxo natural é seguir para a planilha de ramais. Quem só veio editar a
// config de um ambiente existente permanece na tela.
const ambienteNovo = new URLSearchParams(location.search).get('novo') === '1';
if (ambienteNovo) {
  const btn = $('#ec-save');
  if (btn) btn.textContent = 'Salvar e cadastrar ramais →';
}

const FIELDS_STR = [
  'web_user', 'web_password', 'nova_web_user', 'nova_web_password',
  'menu_password', 'keylock_password',
  // Hora: o valor só é usado quando o modo correspondente é "proprio".
  'timezone_mode', 'timezone', 'ntp_mode', 'ntp_server',
];
const FIELDS_INT = ['register_expiration', 'keylock_enable', 'keylock_timeout', 'sip_account'];
const FIELDS_BOOL = ['validar_conectividade', 'verificar_registro_sip'];

// Slots e tipos de tecla são populados por fabricante (catálogo do backend) em
// reload(). Estes são só fallback até o catálogo carregar.
let LINEKEYS = [
  { value: 'LineKey1', label: 'LineKey1' },
  { value: 'LineKey2', label: 'LineKey2' },
  { value: 'LineKey3', label: 'LineKey3' },
  { value: 'LineKey4', label: 'LineKey4' },
];
let TIPOS_FUNCAO = [
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
        ${LINEKEYS.map(k => `<option value="${k.value}"${k.value === fk.key ? ' selected' : ''}>${escAttr(k.label)}</option>`).join('')}
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
  await pintarHora(cfg);

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
    key: (LINEKEYS[0] && LINEKEYS[0].value) || 'LineKey1',
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

  // Opções de tecla programável do FABRICANTE do modelo (catálogo do backend).
  try {
    const cat = await api(`/api/extension-configurator/softkey-catalog?modelo=${encodeURIComponent(modelo)}`);
    if (Array.isArray(cat.key_slots) && cat.key_slots.length) LINEKEYS = cat.key_slots;
    if (Array.isArray(cat.types) && cat.types.length) TIPOS_FUNCAO = cat.types.map((t) => [t.value, t.label]);
    // Gating do seletor de conta: só habilita "Conta 2" onde o fabricante está confirmado.
    const accounts = Array.isArray(cat.accounts) && cat.accounts.length ? cat.accounts : [1];
    const opt2 = document.querySelector('#cfg-sip_account option[value="2"]');
    const hint = $('#cfg-sip_account-hint');
    if (opt2) opt2.disabled = !accounts.includes(2);
    if (hint) hint.textContent = accounts.includes(2)
      ? `${cat.vendor_label || ''}: contas 1 e 2 suportadas.`
      : `${cat.vendor_label || ''}: por enquanto só a conta 1 (Account 2 ainda não mapeada para este fabricante).`;
  } catch (_e) { /* mantém fallback */ }

  const cfg = env.config_padrao || {};
  FIELDS_STR.forEach((k) => { const el = $('#cfg-' + k); if (el) el.value = cfg[k] ?? ''; });
  FIELDS_INT.forEach((k) => { const el = $('#cfg-' + k); if (el) el.value = String(cfg[k] ?? ''); });
  FIELDS_BOOL.forEach((k) => { const el = $('#cfg-' + k); if (el) el.checked = !!cfg[k]; });

  const list = $('#ec-fk-list');
  list.innerHTML = '';
  const fks = Array.isArray(cfg.function_keys) ? cfg.function_keys : [];
  (fks.length ? fks : [defaultFK()]).forEach((fk) => list.appendChild(fkRow(fk)));
}

// ── hora ────────────────────────────────────────────────────────────────────

// Mostrar o valor herdado DENTRO do rótulo da opção é o sinal mais claro de
// herança que existe: o operador lê o que vai acontecer sem procurar em outra
// tela. Campo vazio seria lido como "não configurado", que é a leitura errada.
async function pintarHora(cfg) {
  const modoTz = $('#cfg-timezone_mode');
  const modoNtp = $('#cfg-ntp_mode');
  if (!modoTz || !modoNtp) return;

  modoTz.value = cfg.timezone_mode === 'proprio' ? 'proprio' : 'herdar';
  modoNtp.value = cfg.ntp_mode === 'proprio' ? 'proprio' : 'herdar';

  let herdadoTz = '';
  let herdadoNtp = '';
  try {
    const [fusos, geral] = await Promise.all([
      api('/api/config/timezones'),
      api('/api/config'),
    ]);
    const seletor = $('#cfg-timezone');
    if (seletor && !seletor.childElementCount) {
      seletor.innerHTML = (fusos.zones || [])
        .map((z) => `<option value="${z}">${z}</option>`).join('');
    }
    if (seletor) seletor.value = cfg.timezone || (fusos.detected || {}).name || '';
    herdadoTz = geral.phone_timezone_mode === 'proprio' && geral.phone_timezone
      ? geral.phone_timezone
      : ((fusos.detected || {}).label || '');
    herdadoNtp = geral.phone_ntp_server || 'a.ntp.br';
  } catch (_e) { /* a tela funciona sem o catálogo; só perde a dica */ }

  const hint = $('#cfg-timezone-hint');
  if (hint) {
    hint.textContent = modoTz.value === 'herdar'
      ? `Aplicando ${herdadoTz || 'o fuso da configuração geral'}.`
      : 'Este ambiente ignora a configuração geral.';
  }
  const ntpInput = $('#cfg-ntp_server');
  if (ntpInput) {
    const proprio = modoNtp.value === 'proprio';
    ntpInput.disabled = !proprio;
    // Herdando, o campo exibe o valor real que será aplicado — e não vazio.
    ntpInput.value = proprio ? (cfg.ntp_server || '') : herdadoNtp;
  }
  $('#cfg-timezone')?.classList.toggle('hidden', modoTz.value !== 'proprio');

  const badge = $('#cfg-hora-badge');
  if (badge) {
    const herdando = modoTz.value === 'herdar' && modoNtp.value === 'herdar';
    badge.textContent = herdando ? 'herdado' : 'sobrescrito';
    badge.className = herdando
      ? 'text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-gray-700/60 text-gray-400'
      : 'text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300';
  }
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
    if (ambienteNovo) {
      toast.success('Configuração salva — vamos cadastrar os ramais');
      location.href = `/extension-configurator/environments/${encodeURIComponent(envId)}`;
      return;
    }
    toast.success('Configuração salva');
  } catch (e) {
    toast.error('Erro: ' + e.message);
  }
}

for (const id of ['cfg-timezone_mode', 'cfg-ntp_mode']) {
  document.getElementById(id)?.addEventListener('change', () => pintarHora(collectHora()));
}

// Ao alternar o modo, redesenha com o que está na tela (e não com o que veio do
// servidor), senão a escolha do operador seria descartada na hora.
function collectHora() {
  return {
    timezone_mode: $('#cfg-timezone_mode')?.value,
    ntp_mode: $('#cfg-ntp_mode')?.value,
    timezone: $('#cfg-timezone')?.value,
    ntp_server: $('#cfg-ntp_mode')?.value === 'proprio' ? $('#cfg-ntp_server')?.value : '',
  };
}

$('#ec-save').addEventListener('click', save);
$('#ec-fk-add').addEventListener('click', () => {
  $('#ec-fk-list').appendChild(fkRow(defaultFK()));
});

reload().catch((e) => toast.error('Falha ao carregar: ' + e.message));
