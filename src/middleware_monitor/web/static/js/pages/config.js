import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

let original = null;
const dirty = new Set();
const tokenChanges = {}; // key -> new plaintext (or '' to clear)

const FIELDS = [
  'client_code', 'uscall_host', 'uscall_verify_ssl',
  'webhook_interval_minutes',
  'ping_timeout_ms', 'ping_concurrency', 'device_ping_retention_days',
  'auto_reapply_on_recovery', 'auto_reapply_debounce_minutes',
  'webhook_log_retention_days', 'collection_retention_days', 'system_log_retention_days',
];

function setDirty(on) {
  document.getElementById('cfg-banner').classList.toggle('hidden', !on);
  document.getElementById('cfg-save').disabled = !on;
}

function fillForm(cfg) {
  for (const k of FIELDS) {
    const el = document.getElementById('f-' + k);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = !!cfg[k];
    else el.value = cfg[k] ?? '';
  }
  // masked fields
  renderMasked('uscall_token', cfg.uscall_token === 'set');
  renderWebhooks(cfg.webhooks);
}

function renderMasked(key, isSet) {
  const wrap = document.querySelector(`[data-masked="${key}"]`);
  if (!wrap) return;
  if (tokenChanges[key] !== undefined) {
    wrap.innerHTML = `
      <label class="flex flex-col gap-1.5">
        <span class="text-xs font-semibold text-gray-300">${key}</span>
        <div class="flex gap-2">
          <input value="${tokenChanges[key]}" data-token-input="${key}" class="flex-1 bg-gray-900 ring-1 ring-inset ring-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 font-mono"/>
          <button type="button" data-token-cancel="${key}" class="inline-flex items-center gap-2 rounded-lg font-medium bg-transparent hover:bg-gray-700/60 text-gray-200 ring-1 ring-inset ring-gray-700 px-2.5 py-1.5 text-xs">Cancelar</button>
        </div>
      </label>`;
  } else {
    wrap.innerHTML = `
      <label class="flex flex-col gap-1.5">
        <span class="text-xs font-semibold text-gray-300">${key} <span class="font-normal text-gray-500">sensível</span></span>
        <div class="flex gap-2">
          <input value="${isSet ? '••••••••••••' : ''}" disabled placeholder="${isSet ? '••••••••' : 'não configurado'}" class="flex-1 bg-gray-900 ring-1 ring-inset ring-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 font-mono opacity-60"/>
          <button type="button" data-token-edit="${key}" class="inline-flex items-center gap-2 rounded-lg font-medium bg-gray-800 hover:bg-gray-700 text-gray-200 ring-1 ring-inset ring-gray-700 px-2.5 py-1.5 text-xs">Alterar</button>
        </div>
      </label>`;
  }
  bindTokens();
}

function bindTokens() {
  document.querySelectorAll('[data-token-edit]').forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.tokenEdit;
    tokenChanges[k] = '';
    if (k.startsWith('webhooks.')) renderWebhooks(original.webhooks);
    else renderMasked(k, original[k] === 'set');
    setDirty(true);
  }));
  document.querySelectorAll('[data-token-cancel]').forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.tokenCancel;
    delete tokenChanges[k];
    if (k.startsWith('webhooks.')) renderWebhooks(original.webhooks);
    else renderMasked(k, original[k] === 'set');
    setDirty(Object.keys(tokenChanges).length > 0 || dirty.size > 0);
  }));
  document.querySelectorAll('[data-token-input]').forEach((i) => i.addEventListener('input', (e) => {
    tokenChanges[i.dataset.tokenInput] = e.target.value;
  }));
}

function renderWebhooks(webhooks) {
  const list = document.getElementById('webhooks-list');
  list.innerHTML = Object.entries(webhooks || {}).map(([k, w]) => `
    <div class="bg-gray-900/40 rounded-lg ring-1 ring-gray-700 p-4">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center gap-3">
          <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${k === 'extensions' ? 'bg-blue-500/15 text-blue-400 ring-blue-500/30' : k === 'devices' ? 'bg-green-500/15 text-green-400 ring-green-500/30' : 'bg-indigo-500/15 text-indigo-300 ring-indigo-500/30'}">${k}</span>
          <label class="relative inline-block w-11 h-6 cursor-pointer">
            <input type="checkbox" ${w.enabled ? 'checked' : ''} data-wh-enabled="${k}" class="sr-only peer"/>
            <span class="absolute inset-0 rounded-full bg-gray-700 peer-checked:bg-blue-500 transition"></span>
            <span class="absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform peer-checked:translate-x-5"></span>
          </label>
          <span class="text-sm text-gray-200">${w.enabled ? 'Habilitado' : 'Desligado'}</span>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs text-gray-500">${w.last_status || '—'}</span>
          <button data-wh-test="${k}" class="inline-flex items-center gap-2 rounded-lg font-medium bg-gray-800 hover:bg-gray-700 text-gray-200 ring-1 ring-inset ring-gray-700 px-2.5 py-1.5 text-xs">Testar</button>
        </div>
      </div>
      <div class="grid md:grid-cols-2 gap-3">
        <label class="flex flex-col gap-1.5"><span class="text-xs font-semibold text-gray-300">url</span>
          <input value="${w.url || ''}" data-wh-url="${k}" placeholder="https://…" class="bg-gray-900 ring-1 ring-inset ring-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100"/></label>
        <div data-masked="webhooks.${k}.token"></div>
      </div>
    </div>
  `).join('');
  Object.entries(webhooks || {}).forEach(([k, w]) => renderMasked(`webhooks.${k}.token`, w.token === 'set'));
  list.querySelectorAll('[data-wh-enabled]').forEach((c) => c.addEventListener('change', () => { dirty.add('wh.' + c.dataset.whEnabled); setDirty(true); }));
  list.querySelectorAll('[data-wh-url]').forEach((i) => i.addEventListener('input', () => { dirty.add('wh.' + i.dataset.whUrl + '.url'); setDirty(true); }));
  list.querySelectorAll('[data-wh-test]').forEach((b) => b.addEventListener('click', async () => {
    try { const r = await api('/api/webhooks/test/' + b.dataset.whTest, { method: 'POST' });
      toast[r.ok ? 'success' : 'error'](r.ok ? 'Teste enviado' : 'Falha'); }
    catch { toast.error('Falha'); }
  }));
}

async function load() {
  const cfg = await api('/api/config');
  original = cfg;
  fillForm(cfg);
  dirty.clear();
  for (const k of Object.keys(tokenChanges)) delete tokenChanges[k];
  setDirty(false);
}

FIELDS.forEach((k) => {
  const el = document.getElementById('f-' + k);
  if (!el) return;
  el.addEventListener('input', () => { dirty.add(k); setDirty(true); });
  el.addEventListener('change', () => { dirty.add(k); setDirty(true); });
});

document.getElementById('cfg-reload').addEventListener('click', load);

function readField(k) {
  const el = document.getElementById('f-' + k);
  if (!el) return undefined;
  if (el.type === 'checkbox') return el.checked;
  if (el.type === 'number') {
    const v = el.value.trim();
    return v === '' ? null : +v;
  }
  return el.value;
}

function explainError(e) {
  if (!e) return 'Falha desconhecida.';
  if (e.status === 422 && e.body && Array.isArray(e.body.detail)) {
    return e.body.detail.map((d) => {
      const field = (d.loc || []).filter((x) => x !== 'body').join('.');
      return `${field}: ${d.msg}`;
    }).join('  ·  ');
  }
  if (e.status === 403) return 'Sessão expirou ou falha de segurança (CSRF). Faça logout e login.';
  if (e.status === 401) return 'Não autenticado.';
  return e.message || 'Falha ao salvar.';
}

document.getElementById('cfg-save').addEventListener('click', async () => {
  // Only send fields the user actually changed (or all fields if dirty is global).
  const payload = {};
  for (const k of FIELDS) {
    if (!dirty.has(k)) continue;
    const v = readField(k);
    if (v !== undefined) payload[k] = v;
  }
  if (tokenChanges['uscall_token'] !== undefined) {
    payload.uscall_token = tokenChanges['uscall_token'];
  }
  const webhooks = {};
  ['extensions', 'devices', 'results'].forEach((k) => {
    const en = document.querySelector(`[data-wh-enabled="${k}"]`);
    const url = document.querySelector(`[data-wh-url="${k}"]`);
    const tok = tokenChanges[`webhooks.${k}.token`];
    const upd = {};
    if (en && dirty.has('wh.' + k)) upd.enabled = en.checked;
    if (url && dirty.has('wh.' + k + '.url')) upd.url = url.value.trim();
    if (tok !== undefined) upd.token = tok;
    if (Object.keys(upd).length) webhooks[k] = upd;
  });
  if (Object.keys(webhooks).length) payload.webhooks = webhooks;

  if (Object.keys(payload).length === 0) {
    toast.info('Nada para salvar.');
    return;
  }

  const btn = document.getElementById('cfg-save');
  btn.disabled = true;
  try {
    await api('/api/config', { method: 'PUT', body: payload });
    toast.success('Configuração salva.');
    await load();
  } catch (e) {
    toast.error(explainError(e));
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('cfg-test-uscall').addEventListener('click', async () => {
  const out = document.getElementById('uscall-test-result');
  out.classList.remove('hidden');
  out.className = 'md:col-span-2 rounded-lg px-3 py-2 text-xs bg-gray-700/50 text-gray-300';
  out.textContent = 'Testando…';
  try {
    const r = await api('/api/uscall/test', { method: 'POST', body: { host: document.getElementById('f-uscall_host').value, token: tokenChanges['uscall_token'] } });
    if (r.success) {
      out.className = 'md:col-span-2 rounded-lg px-3 py-2 text-xs bg-green-500/10 ring-1 ring-green-500/30 text-green-300';
      out.textContent = `Conexão OK · HTTP ${r.http_status} · ${r.latency_ms} ms`;
    } else {
      out.className = 'md:col-span-2 rounded-lg px-3 py-2 text-xs bg-red-500/10 ring-1 ring-red-500/30 text-red-300';
      out.textContent = `Falha · ${r.error || 'sem detalhe'}${r.http_status ? ' · HTTP ' + r.http_status : ''}`;
    }
  } catch (e) {
    out.className = 'md:col-span-2 rounded-lg px-3 py-2 text-xs bg-red-500/10 ring-1 ring-red-500/30 text-red-300';
    out.textContent = 'Falha de rede';
  }
});

window.addEventListener('beforeunload', (e) => {
  if (dirty.size > 0 || Object.keys(tokenChanges).length > 0) { e.preventDefault(); return ''; }
});

// --- Identidade visual (logo + favicon) ---
function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function renderBrand(kind, present) {
  const img = document.querySelector(`[data-brand-preview="${kind}"]`);
  const empty = document.querySelector(`[data-brand-empty="${kind}"]`);
  const removeBtn = document.querySelector(`[data-brand-remove="${kind}"]`);
  if (!img) return;
  if (present) {
    img.src = `/api/branding/${kind}?t=${Date.now()}`; // cache-bust
    img.classList.remove('hidden');
    empty?.classList.add('hidden');
    removeBtn?.classList.remove('hidden');
  } else {
    img.removeAttribute('src');
    img.classList.add('hidden');
    empty?.classList.remove('hidden');
    removeBtn?.classList.add('hidden');
  }
}

async function loadBranding() {
  try {
    const st = await api('/api/branding/status');
    renderBrand('logo', !!st.logo);
    renderBrand('favicon', !!st.favicon);
  } catch (_e) { /* ignore */ }
}

async function uploadBrand(kind, file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`/api/branding/${kind}`, {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken() },
    body: fd,
    credentials: 'same-origin',
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_e) { /* ignore */ }
    throw new Error(detail);
  }
}

document.querySelectorAll('[data-brand-input]').forEach((inp) => {
  inp_wire(inp);
});
function inp_wire(inp) {
  const kind = inp.dataset.brandInput;
  inp.addEventListener('change', async () => {
    const file = inp.files && inp.files[0];
    if (!file) return;
    try {
      await uploadBrand(kind, file);
      renderBrand(kind, true);
      toast.success(`${kind === 'logo' ? 'Logo' : 'Favicon'} atualizado`);
    } catch (err) {
      toast.error('Erro: ' + err.message);
    } finally {
      inp.value = '';
    }
  });
}

document.querySelectorAll('[data-brand-remove]').forEach((btn) => {
  const kind = btn.dataset.brandRemove;
  btn.addEventListener('click', async () => {
    try {
      await api(`/api/branding/${kind}`, { method: 'DELETE' });
      renderBrand(kind, false);
      toast.success('Removido');
    } catch (err) {
      toast.error('Erro ao remover: ' + err.message);
    }
  });
});

loadBranding();

document.getElementById('auto-link-btn')?.addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  const out = document.getElementById('auto-link-result');
  btn.disabled = true;
  out.classList.remove('hidden');
  out.className = 'mt-3 rounded-lg px-3 py-2 text-xs bg-gray-700/50 text-gray-300';
  out.textContent = 'Vinculando…';
  try {
    const r = await api('/api/devices/auto-link', { method: 'POST' });
    out.className = 'mt-3 rounded-lg px-3 py-2 text-xs bg-green-500/10 ring-1 ring-green-500/30 text-green-300';
    out.textContent = r.linked ? `${r.linked} linha(s) vinculada(s) por IP.` : 'Nenhuma linha órfã com IP igual a algum device.';
  } catch (err) {
    out.className = 'mt-3 rounded-lg px-3 py-2 text-xs bg-red-500/10 ring-1 ring-red-500/30 text-red-300';
    out.textContent = 'Falha ao executar auto-link.';
  } finally {
    btn.disabled = false;
  }
});

load();
