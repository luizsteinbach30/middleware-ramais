// Configuração assistida do coletor MQTT.
//
// A promessa da tela é que ninguém precisa saber o que é porta, transporte ou
// TLS: o operador digita o endereço, clica em "Descobrir conexão" e o servidor
// sonda a rede e devolve o que encontrou. O mesmo vale para o tópico — em vez
// de digitar um palpite, ele escolhe entre os que existem no broker.
//
// CRUD imediato (não passa pelo botão "Salvar configuração"), igual ao padrão
// dos servidores USCall.

import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const el = (id) => document.getElementById(id);
const STATE_LABEL = {
  connected: ['conectado', 'bg-green-500/15 text-green-300 ring-green-500/30'],
  subscribed: ['coletando', 'bg-green-500/15 text-green-300 ring-green-500/30'],
  connecting: ['conectando…', 'bg-amber-500/15 text-amber-300 ring-amber-500/30'],
  disconnected: ['sem conexão', 'bg-red-500/15 text-red-300 ring-red-500/30'],
  error: ['erro', 'bg-red-500/15 text-red-300 ring-red-500/30'],
};

let editingId = null;      // null = novo broker
let discovered = null;     // endpoint aceito pela sonda
let certToTrust = null;    // impressão digital que o operador confiou
let brokers = [];
let statusTimer = null;

// ── listagem ────────────────────────────────────────────────────────────────

function renderList() {
  const list = el('mq-list');
  if (!list) return;
  if (!brokers.length) {
    list.innerHTML = '<div class="text-xs text-gray-500">Nenhum broker configurado — nada está sendo registrado.</div>';
    return;
  }
  list.innerHTML = brokers.map((b) => `
    <div class="bg-gray-900/40 rounded-lg ring-1 ring-gray-700 p-4 flex items-center gap-3 flex-wrap">
      <div class="flex-1 min-w-[220px]">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-sm font-medium text-gray-100">${esc(b.nome)}</span>
          <span data-mq-pill="${b.id}" class="px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-600/30 text-gray-400 ring-1 ring-inset ring-gray-600">—</span>
          ${b.enabled ? '' : '<span class="px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-600/30 text-gray-400 ring-1 ring-inset ring-gray-600">desligado</span>'}
          ${b.tls_fingerprint ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-medium bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-500/30" title="certificado fixado">cert. fixado</span>' : ''}
        </div>
        <div class="text-xs text-gray-500 mt-0.5 font-mono">${esc(endpointLabel(b))}</div>
        <div class="text-xs text-gray-500 mt-0.5">tópicos: <span class="font-mono">${b.topics.map(esc).join(', ')}</span></div>
        <div data-mq-detail="${b.id}" class="text-[11px] text-gray-500 mt-1"></div>
      </div>
      <div class="flex items-center gap-2">
        <button data-mq-edit="${b.id}" class="inline-flex items-center gap-2 rounded-lg font-medium bg-gray-800 hover:bg-gray-700 text-gray-200 ring-1 ring-inset ring-gray-700 px-2.5 py-1.5 text-xs">Editar</button>
        <button data-mq-del="${b.id}" class="inline-flex items-center gap-2 rounded-lg font-medium text-red-300 hover:bg-red-500/10 ring-1 ring-inset ring-red-500/30 px-2.5 py-1.5 text-xs">Remover</button>
      </div>
    </div>`).join('');

  list.querySelectorAll('[data-mq-edit]').forEach((b) => b.addEventListener('click', () => {
    const broker = brokers.find((x) => x.id === +b.dataset.mqEdit);
    if (broker) openWizard(broker);
  }));
  list.querySelectorAll('[data-mq-del]').forEach((b) => b.addEventListener('click', async () => {
    const broker = brokers.find((x) => x.id === +b.dataset.mqDel);
    if (!broker) return;
    if (!confirm(`Remover o broker "${broker.nome}"? O coletor para de gravar; as mensagens já registradas permanecem.`)) return;
    try {
      await api(`/api/mqtt/brokers/${broker.id}`, { method: 'DELETE' });
      toast.success('Broker removido.');
      await loadBrokers();
    } catch (e) { toast.error(`Falha ao remover: ${e.message}`); }
  }));
}

function endpointLabel(b) {
  if (b.transport === 'websockets') return `${b.tls ? 'wss' : 'ws'}://${b.host}:${b.port}${b.ws_path || '/mqtt'}`;
  return `${b.tls ? 'ssl' : 'tcp'}://${b.host}:${b.port}`;
}

async function loadBrokers() {
  try {
    brokers = await api('/api/mqtt/brokers');
  } catch (_e) {
    brokers = [];
  }
  renderList();
  await refreshStatus();
}

// ── estado do coletor ───────────────────────────────────────────────────────

function fmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), u.length - 1);
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
}

async function refreshStatus() {
  const pill = el('mq-state');
  if (!pill) return;
  let s;
  try { s = await api('/api/mqtt/status'); } catch (_e) { return; }

  const uso = el('mq-usage');
  if (uso) {
    uso.textContent = `${s.stored_messages.toLocaleString('pt-BR')} mensagens guardadas · ${fmtBytes(s.stored_payload_bytes)} de payload`;
  }

  if (!s.configured) {
    pill.className = 'shrink-0 px-2.5 py-1 rounded-full text-[11px] font-medium bg-gray-600/30 text-gray-400 ring-1 ring-inset ring-gray-600 whitespace-nowrap';
    pill.textContent = 'não configurado';
    return;
  }
  const principal = s.brokers[0];
  const [rotulo, cor] = STATE_LABEL[principal?.state] || ['sem conexão', STATE_LABEL.disconnected[1]];
  pill.className = `shrink-0 px-2.5 py-1 rounded-full text-[11px] font-medium ring-1 ring-inset whitespace-nowrap ${cor}`;
  pill.textContent = s.per_minute ? `${rotulo} · ${s.per_minute} msg/min` : rotulo;

  for (const b of s.brokers) {
    const pillB = document.querySelector(`[data-mq-pill="${b.broker_id}"]`);
    if (pillB) {
      const [rot, c] = STATE_LABEL[b.state] || ['sem conexão', STATE_LABEL.disconnected[1]];
      pillB.className = `px-2 py-0.5 rounded-full text-[10px] font-medium ring-1 ring-inset ${c}`;
      pillB.textContent = rot;
    }
    const det = document.querySelector(`[data-mq-detail="${b.broker_id}"]`);
    if (det) {
      const partes = [esc(b.detail || '')];
      if (b.connected_since) partes.push(`conectado desde ${fmtTs(b.connected_since)}`);
      if (s.last_message_at) partes.push(`última mensagem ${fmtTs(s.last_message_at)}`);
      if (s.avg_lag_seconds !== null && s.avg_lag_seconds !== undefined) partes.push(`atraso médio ${s.avg_lag_seconds}s`);
      if (s.clock_outliers) partes.push(`<span class="text-amber-300">${s.clock_outliers} com hora do PBX fora do relógio do servidor</span>`);
      if (s.dropped) partes.push(`<span class="text-red-300">${s.dropped} descartadas por fila cheia</span>`);
      if (s.persist_failures) partes.push(`<span class="text-red-300">${s.persist_failures} falhas de gravação</span>`);
      det.innerHTML = partes.filter(Boolean).join(' · ');
    }
  }
}

// ── assistente ──────────────────────────────────────────────────────────────

function openWizard(broker) {
  editingId = broker ? broker.id : null;
  discovered = broker
    ? { host: broker.host, port: broker.port, transport: broker.transport, tls: broker.tls, ws_path: broker.ws_path }
    : null;
  certToTrust = broker ? broker.tls_fingerprint : null;

  el('mq-wizard').classList.remove('hidden');
  el('mq-add').classList.add('hidden');
  el('mq-address').value = broker ? (broker.address_input || `${broker.host}:${broker.port}`) : '';
  el('mq-user').value = broker ? broker.username : '';
  el('mq-pass').value = '';
  el('mq-pass').placeholder = broker && broker.password === 'set' ? '•••••••• (mantida)' : '';
  el('mq-nome').value = broker ? broker.nome : '';
  el('mq-enabled').checked = broker ? broker.enabled : true;
  el('mq-topics-manual').value = broker ? broker.topics.join('\n') : '';
  el('mq-report').classList.add('hidden');
  el('mq-cert').classList.add('hidden');
  el('mq-topics').innerHTML = 'Clique em "Procurar tópicos" para ver o que existe no broker e escolher — ou digite o filtro abaixo.';
  el('mq-topics-box').classList.toggle('hidden', !broker);
  el('mq-save').disabled = !broker;
}

function closeWizard() {
  el('mq-wizard').classList.add('hidden');
  el('mq-add').classList.remove('hidden');
  editingId = null;
  discovered = null;
  certToTrust = null;
}

function linhaRelatorio(r) {
  const marca = r.mqtt_ok ? 'ok' : (r.auth_required ? 'recusado' : 'não');
  const extra = r.latency_ms !== null && r.latency_ms !== undefined ? ` · ${r.latency_ms} ms` : '';
  return `  ${String(r.port).padEnd(6)} ${r.tls ? 'TLS' : '   '} ${r.transport === 'websockets' ? 'ws' : '  '}  ${marca.padEnd(9)} ${r.detail}${extra}`;
}

async function descobrir() {
  const address = el('mq-address').value.trim();
  if (!address) { toast.error('Informe o endereço do broker.'); return; }
  const btn = el('mq-discover');
  const rep = el('mq-report');
  btn.disabled = true;
  rep.classList.remove('hidden');
  rep.textContent = 'Sondando o broker… (isso leva alguns segundos)';
  el('mq-cert').classList.add('hidden');

  try {
    const r = await api('/api/mqtt/discover', {
      method: 'POST',
      body: {
        address,
        username: el('mq-user').value.trim(),
        password: el('mq-pass').value || null,
        broker_id: editingId,
      },
    });
    const linhas = [`Endereço informado ... ${r.address_input}`];
    linhas.push(r.dns_error ? `DNS .................. falhou: ${r.dns_error}` : `DNS .................. ${r.resolved.join(', ')}`);
    if (r.results.length) {
      linhas.push('Portas sondadas:');
      linhas.push(...r.results.map(linhaRelatorio));
    }
    if (r.chosen) {
      linhas.push('');
      linhas.push(`Escolhido ............ ${r.chosen.label}`);
      linhas.push(`Autenticação ......... ${r.chosen.detail}`);
    }
    if (r.notes.length) { linhas.push(''); linhas.push(...r.notes.map((n) => `» ${n}`)); }
    rep.textContent = linhas.join('\n');

    if (r.chosen) {
      discovered = {
        host: r.chosen.host, port: r.chosen.port, transport: r.chosen.transport,
        tls: r.chosen.tls, ws_path: r.chosen.ws_path,
      };
      if (!el('mq-nome').value.trim()) el('mq-nome').value = r.chosen.host;
      el('mq-topics-box').classList.remove('hidden');
      el('mq-save').disabled = false;
      if (r.needs_cert_trust && r.chosen.cert_fingerprint) mostrarCertificado(r.chosen);
      else certToTrust = null;
      toast.success(`Conexão encontrada em ${r.chosen.label}`);
    } else {
      discovered = null;
      el('mq-save').disabled = true;
      toast.error(r.needs_credentials ? 'O broker pediu usuário e senha.' : 'Nenhuma porta respondeu MQTT.');
    }
  } catch (e) {
    rep.textContent = `Falha ao sondar: ${e.message}`;
    el('mq-save').disabled = true;
  } finally {
    btn.disabled = false;
  }
}

function mostrarCertificado(chosen) {
  // Certificado não assinado por CA conhecida: em vez de um "ignorar TLS"
  // genérico, o operador confia naquele certificado — e só nele.
  const box = el('mq-cert');
  box.classList.remove('hidden');
  box.innerHTML = `
    <div class="font-semibold">Certificado não assinado por uma autoridade conhecida</div>
    <div class="font-mono text-[11px] leading-relaxed">
      ${esc(chosen.cert_subject || '(sem identificação)')}<br/>
      emissor: ${esc(chosen.cert_issuer || '—')}<br/>
      validade até: ${esc(chosen.cert_not_after || '—')}<br/>
      SHA-256: ${esc(chosen.cert_fingerprint)}
    </div>
    <label class="flex items-center gap-2 cursor-pointer">
      <input type="checkbox" id="mq-trust" class="accent-amber-500 w-4 h-4"/>
      <span>Confio neste certificado (só ele será aceito nas conexões)</span>
    </label>`;
  const chk = el('mq-trust');
  chk.checked = certToTrust === chosen.cert_fingerprint;
  chk.addEventListener('change', () => {
    certToTrust = chk.checked ? chosen.cert_fingerprint : null;
  });
}

async function procurarTopicos() {
  if (!discovered) { toast.error('Descubra a conexão primeiro.'); return; }
  const btn = el('mq-sniff');
  const box = el('mq-topics');
  btn.disabled = true;
  box.innerHTML = 'Escutando o broker por 8 segundos…';
  try {
    const r = await api('/api/mqtt/sniff', {
      method: 'POST',
      body: {
        broker_id: editingId,
        host: discovered.host, port: discovered.port, transport: discovered.transport,
        tls: discovered.tls, ws_path: discovered.ws_path,
        username: el('mq-user').value.trim(),
        password: el('mq-pass').value || null,
        tls_verify: !certToTrust,
        tls_fingerprint: certToTrust,
        seconds: 8,
        filter: el('mq-topics-manual').value.split('\n')[0].trim(),
      },
    });
    if (!r.success) { box.innerHTML = `<span class="text-red-300">${esc(r.error)}</span>`; return; }
    if (!r.branches.length) {
      box.innerHTML = `Nada foi publicado em ${r.seconds}s${r.filter_used ? ` no ramo <span class="font-mono">${esc(r.filter_used)}</span>` : ''}. Dá para tentar de novo ou digitar o filtro à mão abaixo.`;
      return;
    }
    // Quando a ACL do broker nega "#", o operador precisa saber onde a escuta
    // realmente aconteceu — senão a lista parece incompleta sem explicação.
    const aviso = r.denied.length
      ? `<div class="text-amber-300">O broker não deixou escutar <span class="font-mono">${r.denied.map(esc).join(', ')}</span> (política dele); escutei em <span class="font-mono">${esc(r.filter_used)}</span>.</div>`
      : '';
    const marcados = new Set(el('mq-topics-manual').value.split('\n').map((s) => s.trim()).filter(Boolean));
    box.innerHTML = `
      ${aviso}
      <div class="text-gray-400">${r.messages} mensagens em ${r.topics} tópicos nos últimos ${r.seconds}s:</div>
      ${r.branches.map((b, i) => `
        <label class="flex items-start gap-2 cursor-pointer bg-gray-900/60 rounded-lg px-3 py-2 ring-1 ring-gray-700">
          <input type="checkbox" data-mq-topic="${esc(b.filter)}" class="accent-blue-500 w-4 h-4 mt-0.5" ${(marcados.has(b.filter) || b.recognized || (!marcados.size && i === 0)) ? 'checked' : ''}/>
          <span class="min-w-0">
            <span class="font-mono text-gray-200">${esc(b.filter)}</span>
            <span class="text-gray-500"> · ${b.messages} msg · ${b.topics} tópico(s)</span>
            ${b.recognized ? '<span class="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-blue-500/15 text-blue-300">status de ramal</span>' : ''}
            ${b.sample_payload ? `<span class="block text-[11px] text-gray-600 font-mono truncate">${esc(b.sample_payload.slice(0, 120))}</span>` : ''}
          </span>
        </label>`).join('')}`;
    sincronizarManual();
    box.querySelectorAll('[data-mq-topic]').forEach((c) => c.addEventListener('change', sincronizarManual));
  } catch (e) {
    box.innerHTML = `<span class="text-red-300">Falha: ${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}

function sincronizarManual() {
  const marcados = [...document.querySelectorAll('[data-mq-topic]:checked')].map((c) => c.dataset.mqTopic);
  if (marcados.length) el('mq-topics-manual').value = marcados.join('\n');
}

async function salvar() {
  if (!discovered) { toast.error('Descubra a conexão antes de salvar.'); return; }
  const topics = el('mq-topics-manual').value.split('\n').map((s) => s.trim()).filter(Boolean);
  if (!topics.length) { toast.error('Escolha ao menos um tópico para gravar.'); return; }

  const senha = el('mq-pass').value;
  const body = {
    nome: el('mq-nome').value.trim() || discovered.host,
    address_input: el('mq-address').value.trim(),
    host: discovered.host,
    port: discovered.port,
    transport: discovered.transport,
    tls: discovered.tls,
    ws_path: discovered.ws_path,
    username: el('mq-user').value.trim(),
    // vazio ao editar = manter a senha gravada
    password: senha ? senha : (editingId ? 'set' : ''),
    tls_verify: !certToTrust,
    tls_fingerprint: certToTrust,
    topics,
    qos: 1,
    enabled: el('mq-enabled').checked,
  };
  const btn = el('mq-save');
  btn.disabled = true;
  try {
    if (editingId) await api(`/api/mqtt/brokers/${editingId}`, { method: 'PUT', body });
    else await api('/api/mqtt/brokers', { method: 'POST', body });
    toast.success('Broker salvo — o coletor já está reconectando.');
    closeWizard();
    await loadBrokers();
  } catch (e) {
    toast.error(`Falha ao salvar: ${e.message}`);
    btn.disabled = false;
  }
}

// ── bootstrap ───────────────────────────────────────────────────────────────

if (el('mq-list')) {
  el('mq-add').addEventListener('click', () => openWizard(null));
  el('mq-cancel').addEventListener('click', closeWizard);
  el('mq-discover').addEventListener('click', descobrir);
  el('mq-sniff').addEventListener('click', procurarTopicos);
  el('mq-save').addEventListener('click', salvar);
  el('mq-address').addEventListener('keydown', (e) => { if (e.key === 'Enter') descobrir(); });
  loadBrokers();
  statusTimer = setInterval(refreshStatus, 5000);
  window.addEventListener('beforeunload', () => clearInterval(statusTimer));
}
