import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const $ = (s) => document.querySelector(s);
const envId = window.EC_ENV_ID;

const FIELDS_STR = [
  'sip_server', 'web_user', 'web_password', 'nova_web_user', 'nova_web_password',
  'menu_password', 'keylock_password',
];
const FIELDS_INT = ['register_expiration', 'keylock_enable', 'keylock_timeout'];
const FIELDS_BOOL = ['validar_conectividade'];

async function reload() {
  const env = await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}`);
  $('#ec-title').textContent = `Config padrão — ${env.nome}`;
  $('#ec-back').href = `/extension-configurator/environments/${encodeURIComponent(envId)}`;
  const cfg = env.config_padrao || {};
  FIELDS_STR.forEach((k) => { const el = $('#cfg-' + k); if (el) el.value = cfg[k] ?? ''; });
  FIELDS_INT.forEach((k) => { const el = $('#cfg-' + k); if (el) el.value = String(cfg[k] ?? ''); });
  FIELDS_BOOL.forEach((k) => { const el = $('#cfg-' + k); if (el) el.checked = !!cfg[k]; });
}

async function save() {
  const body = { config_padrao: {} };
  FIELDS_STR.forEach((k) => { body.config_padrao[k] = $('#cfg-' + k).value; });
  FIELDS_INT.forEach((k) => { body.config_padrao[k] = parseInt($('#cfg-' + k).value || '0', 10); });
  FIELDS_BOOL.forEach((k) => { body.config_padrao[k] = $('#cfg-' + k).checked; });
  try {
    await api(`/api/extension-configurator/environments/${encodeURIComponent(envId)}`, {
      method: 'PUT', body,
    });
    toast({ tone: 'success', text: 'Configuração salva' });
  } catch (e) {
    toast({ tone: 'error', text: 'Erro: ' + e.message });
  }
}

$('#ec-save').addEventListener('click', save);
reload().catch((e) => toast({ tone: 'error', text: 'Falha ao carregar: ' + e.message }));
