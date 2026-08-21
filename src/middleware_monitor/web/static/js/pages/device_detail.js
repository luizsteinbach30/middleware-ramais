import { api } from '/static/js/api.js';
import { latencyChart } from '/static/js/components/charts.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

const id = location.pathname.split('/').pop();
let window_ = '24h';

function badge(tone, text, dot = true) {
  // amber/sky existem para o estado de telefonia usar as mesmas cores do
  // painel ao vivo — o mesmo estado não pode ter cor diferente em cada tela.
  const tones = { green: 'bg-green-500/15 text-green-400 ring-green-500/30', red: 'bg-red-500/15 text-red-400 ring-red-500/30', blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30', yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30', amber: 'bg-amber-500/15 text-amber-300 ring-amber-500/30', sky: 'bg-sky-500/15 text-sky-300 ring-sky-500/30', gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30' };
  const dots = { green: 'bg-green-400', red: 'bg-red-400', blue: 'bg-blue-400', yellow: 'bg-yellow-400', amber: 'bg-amber-400', sky: 'bg-sky-400', gray: 'bg-gray-400' };
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${tones[tone]}">${dot ? `<span class="w-1.5 h-1.5 rounded-full ${dots[tone]}"></span>` : ''}${text}</span>`;
}

// Estado de telefonia (MQTT). Só aparece quando o ramal está de fato em uso: um
// selo "disponível" ao lado do status lógico "disponível" seria redundante.
const TELEFONIA = {
  tocando: ['amber', 'tocando'],
  discando: ['sky', 'discando'],
  ocupado: ['blue', 'em conversa'],
};

function telefonia(d) {
  const par = TELEFONIA[d.telephony_status];
  if (!par) return '';
  const alvo = d.telephony_numero ? ` ${d.telephony_numero}` : '';
  return badge(par[0], par[1] + alvo);
}

// ── Telefonia (coletor MQTT) ────────────────────────────────────────────────

const RESULTADO_TEL = {
  atendida: ['text-green-300', 'atendida'],
  perdida: ['text-red-300', 'perdida'],
  nao_atendida: ['text-amber-300', 'não atenderam'],
  em_curso: ['text-blue-300', 'em curso'],
  indeterminada: ['text-gray-500', 'indeterminada'],
};

function durTel(seg) {
  if (seg === null || seg === undefined) return '—';
  const s = Math.max(0, Math.floor(seg));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}min ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}min`;
}

function tel(s) {
  return String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function cartaoResumo(rotulo, valor, cor) {
  return `<div class="rounded-lg px-3 py-2.5 bg-gray-900/60 ring-1 ring-inset ring-gray-700">
    <div class="text-[10px] uppercase tracking-wider text-gray-500">${tel(rotulo)}</div>
    <div class="text-xl font-semibold tabular-nums mt-0.5 ${cor || 'text-gray-100'}">${tel(valor)}</div>
  </div>`;
}

async function carregarTelefonia(ramal) {
  const card = document.getElementById('dd-tel-card');
  const corpo = document.getElementById('dd-tel-body');
  const resumo = document.getElementById('dd-tel-resumo');
  const links = document.getElementById('dd-tel-links');
  if (!card) return;

  const hoje = new Date();
  const dia = `${hoje.getFullYear()}-${String(hoje.getMonth() + 1).padStart(2, '0')}-${String(hoje.getDate()).padStart(2, '0')}`;

  let chamadas = { items: [], total: 0 };
  let stats = [];
  try {
    // `ramal_exato`: sem isso, o ramal 9959 traria as chamadas do 19959 — e
    // chamada de outro ramal nesta página seria erro, não conveniência.
    [chamadas, stats] = await Promise.all([
      api(`/api/mqtt/calls?last=24h&limit=10&ramal_exato=true&ramal=${encodeURIComponent(ramal)}`),
      api(`/api/mqtt/calls/daily?dia=${dia}&ramal=${encodeURIComponent(ramal)}`),
    ]);
  } catch (_e) {
    corpo.innerHTML = '<span class="text-gray-500">Não foi possível consultar as chamadas.</span>';
    return;
  }

  const d = stats[0];
  resumo.innerHTML = d
    ? [
        cartaoResumo('Chamadas hoje', d.chamadas),
        cartaoResumo('Atendidas', d.atendidas, 'text-green-300'),
        cartaoResumo('Perdidas', d.perdidas, d.perdidas ? 'text-red-300' : null),
        cartaoResumo('Recebidas / feitas', `${d.entrantes} / ${d.saintes}`),
        cartaoResumo('Em conversa', durTel(d.talk_seconds)),
      ].join('')
    : '';

  links.innerHTML = `
    <a href="/mqtt-chamadas?ramal=${encodeURIComponent(ramal)}" class="inline-flex items-center gap-1.5 rounded-lg font-medium bg-transparent hover:bg-gray-700/60 text-gray-200 ring-1 ring-inset ring-gray-700 px-2.5 py-1.5 text-xs">Todas as chamadas</a>
    <a href="/mqtt-messages?ramal=${encodeURIComponent(ramal)}" class="inline-flex items-center gap-1.5 rounded-lg font-medium bg-transparent hover:bg-gray-700/60 text-gray-200 ring-1 ring-inset ring-gray-700 px-2.5 py-1.5 text-xs">Mensagens cruas</a>`;

  if (!chamadas.items.length) {
    // Distingue "não há coletor" de "o ramal não falou": são problemas
    // diferentes e a ação do operador é outra em cada caso.
    corpo.innerHTML = d
      ? '<span class="text-gray-500">Nenhuma chamada nas últimas 24 horas.</span>'
      : '<span class="text-gray-500">Sem dados de telefonia para este ramal. Se o coletor MQTT ainda não foi configurado, comece pela <a class="underline" href="/config">configuração</a>.</span>';
    return;
  }

  corpo.innerHTML = `<table class="w-full text-sm">
    <thead class="text-gray-500 text-xs uppercase tracking-wider">
      <tr>
        <th class="text-left py-2 font-semibold">Início</th>
        <th class="text-left py-2 font-semibold">Direção</th>
        <th class="text-left py-2 font-semibold">Outra ponta</th>
        <th class="text-left py-2 font-semibold">Resultado</th>
        <th class="text-right py-2 font-semibold">Toque</th>
        <th class="text-right py-2 font-semibold">Conversa</th>
      </tr>
    </thead>
    <tbody class="divide-y divide-gray-800">
      ${chamadas.items.map((c) => {
        const [cor, rotulo] = RESULTADO_TEL[c.outcome] || RESULTADO_TEL.indeterminada;
        const dir = c.direcao === 'entrante' ? 'recebida' : c.direcao === 'sainte' ? 'feita' : '—';
        return `<tr>
          <td class="py-2 font-mono text-xs text-gray-300">${tel(fmtTs(c.started_at))}</td>
          <td class="py-2 text-xs text-gray-400">${dir}</td>
          <td class="py-2 font-mono text-xs text-gray-300">${c.numero ? tel(c.numero) : '<span class="text-gray-600">—</span>'}</td>
          <td class="py-2 text-xs ${cor}">${rotulo}</td>
          <td class="py-2 text-right tabular-nums text-xs text-gray-400">${durTel(c.ring_seconds)}</td>
          <td class="py-2 text-right tabular-nums text-xs text-gray-200">${durTel(c.talk_seconds)}</td>
        </tr>`;
      }).join('')}
    </tbody>
  </table>
  ${chamadas.total > chamadas.items.length
    ? `<div class="mt-3 text-xs text-gray-500">Mostrando ${chamadas.items.length} de ${chamadas.total} nas últimas 24 horas.</div>`
    : ''}`;
}


async function load() {
  try {
    const [d, hist, pings] = await Promise.all([
      api('/api/devices/' + id),
      api(`/api/devices/${id}/history?window=${window_}`),
      api(`/api/devices/${id}/pings?limit=20`),
    ]);
    document.getElementById('dd-title').textContent = `Ramal ${d.name}`;
    // O nome do device E o numero do ramal (RF-12): e por ele que a
    // telefonia e consultada.
    carregarTelefonia(d.name);
    document.getElementById('dd-subtitle').textContent = `${d.ip || '—'} · ${d.model || '—'}`;
    document.getElementById('dd-cards').innerHTML = `
      <div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Status lógico</div>
        <div class="mt-2 flex items-center gap-1.5 flex-wrap">${d.logical_status === 'available' ? badge('blue', 'disponível') : d.logical_status === 'unavailable' ? badge('yellow', 'indisponível') : badge('gray', '—')}${telefonia(d)}</div>
        <div class="text-xs text-gray-500 mt-2">Último seen ${fmtTs(d.last_seen_at)}${d.status_source === 'mqtt' ? ' · via MQTT' : d.status_source === 'uscall' ? ' · via coleta USCall' : ''}</div>
      </div>
      <div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Rede</div>
        <div class="mt-2">${d.network_status === 'online' ? badge('green', 'online') : d.network_status === 'offline' ? badge('red', 'offline') : badge('gray', '—')}</div>
        <div class="text-xs text-gray-500 mt-2">Último ping ${fmtTs(d.last_ping_at)}</div>
      </div>
      <div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Latência atual</div>
        <div class="text-2xl font-bold text-blue-300 mt-1 tabular-nums">${d.latency_ms ?? '—'}<span class="text-sm text-gray-500 ml-1">ms</span></div>
      </div>
      <div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">MAC</div>
        <div class="font-mono text-sm text-gray-200 mt-2">${d.mac || '—'}</div>
        <div class="text-xs text-gray-500 mt-1">Detectado por ARP</div>
      </div>`;

    document.getElementById('dd-chart-info').textContent = `${hist.granularity} · ${hist.points.length} pontos`;
    const points = hist.points.map((p) => ({ ms: p.online_ratio === 0 ? null : p.latency_ms_avg, online: p.online_ratio > 0 }));
    latencyChart(document.getElementById('dd-chart'), points);

    document.getElementById('dd-pings').innerHTML = pings.map((p) => `
      <tr class="hover:bg-gray-700/30">
        <td class="px-4 py-2 text-gray-300 font-mono text-xs">${fmtTs(p.timestamp)}</td>
        <td class="px-4 py-2">${p.online ? badge('green', 'online') : badge('red', 'offline')}</td>
        <td class="px-4 py-2 text-right font-mono tabular-nums text-gray-300">${p.latency_ms != null ? p.latency_ms + ' ms' : '—'}</td>
      </tr>
    `).join('');
    injectIcons();
  } catch (_e) {}
}

document.querySelectorAll('#dd-window button').forEach((b) => {
  b.addEventListener('click', () => {
    document.querySelectorAll('#dd-window button').forEach((x) => {
      x.classList.remove('bg-blue-500', 'text-white');
      x.classList.add('text-gray-400', 'hover:text-gray-200');
    });
    b.classList.add('bg-blue-500', 'text-white');
    b.classList.remove('text-gray-400', 'hover:text-gray-200');
    window_ = b.dataset.w;
    load();
  });
});

document.querySelector('[data-action="ping-now"]')?.addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try { await api(`/api/devices/${id}/refresh`, { method: 'POST' }); toast.success('Ping disparado'); load(); }
  catch (err) { toast.error('Falha'); }
  finally { btn.disabled = false; }
});

// ---------------------------------------------------------------------------
// Configurador de ramais (vinculação + apply + histórico)
// ---------------------------------------------------------------------------

function statusBadge(s) {
  if (s === 'ok' || s === 'applied') return badge('green', 'ok');
  if (s === 'outdated') return badge('yellow', 'desatualizado');
  if (s === 'erro' || s === 'error') return badge('red', 'erro');
  if (s === 'running') return badge('blue', 'em andamento');
  return badge('gray', s || 'pendente');
}

function renderLinkedEmpty() {
  document.getElementById('ec-actions').innerHTML = `
    <button data-action="link-open" class="inline-flex items-center gap-2 rounded-lg font-medium bg-blue-500 hover:bg-blue-400 text-white px-2.5 py-1.5 text-xs">
      <span data-icon="edit"></span>Vincular linha
    </button>`;
  document.getElementById('ec-body').innerHTML = `
    <div class="rounded-lg ring-1 ring-gray-700 bg-gray-900/40 px-4 py-3 text-xs text-gray-400">
      Este ramal não está vinculado a nenhuma linha do Configurador de Ramais.
      Vincule manualmente — ou aguarde o auto-link por IP no próximo ciclo do USCall.
    </div>`;
  injectIcons();
  document.querySelector('[data-action="link-open"]')?.addEventListener('click', openLinkModal);
}

function renderLinked(line, events) {
  document.getElementById('ec-actions').innerHTML = `
    <button data-action="apply-config" class="inline-flex items-center gap-2 rounded-lg font-medium bg-blue-500 hover:bg-blue-400 text-white px-2.5 py-1.5 text-xs">
      <span data-icon="play"></span>Importar config
    </button>
    <button data-action="unlink" class="inline-flex items-center gap-2 rounded-lg font-medium bg-transparent hover:bg-gray-700/60 text-gray-200 ring-1 ring-inset ring-gray-700 px-2.5 py-1.5 text-xs">
      <span data-icon="x"></span>Desvincular
    </button>`;

  const ts = line.ultima_aplicacao ? fmtTs(line.ultima_aplicacao) : '—';
  const erroLine = line.ultimo_erro
    ? `<div class="mt-2 text-xs text-red-300 bg-red-500/5 ring-1 ring-red-500/30 rounded px-3 py-2"><strong>Erro:</strong> ${line.ultimo_erro}</div>`
    : '';
  const eventsHTML = events.length
    ? `<div class="mt-4">
         <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold mb-2">Histórico de reaplicação</div>
         <div class="ring-1 ring-gray-700 rounded-lg overflow-hidden">
           <table class="w-full text-xs">
             <thead class="bg-gray-900/60 text-gray-400 uppercase tracking-wider">
               <tr><th class="text-left px-3 py-2">Quando</th><th class="text-left px-3 py-2">Motivo</th><th class="text-left px-3 py-2">Status</th><th class="text-left px-3 py-2">Erro</th></tr>
             </thead>
             <tbody class="divide-y divide-gray-800">
               ${events.map((e) => `
                 <tr class="hover:bg-gray-700/30">
                   <td class="px-3 py-1.5 text-gray-300 font-mono">${fmtTs(e.started_at)}</td>
                   <td class="px-3 py-1.5 text-gray-300">${e.reason === 'recovery' ? 'recovery (auto)' : 'manual'}</td>
                   <td class="px-3 py-1.5">${statusBadge(e.status)}</td>
                   <td class="px-3 py-1.5 text-red-300 truncate max-w-xs" title="${(e.error || '').replace(/"/g, '&quot;')}">${e.error || '—'}</td>
                 </tr>`).join('')}
             </tbody>
           </table>
         </div>
       </div>`
    : `<div class="mt-3 text-xs text-gray-500">Nenhum reapply automático registrado ainda.</div>`;

  document.getElementById('ec-body').innerHTML = `
    <div class="grid md:grid-cols-2 gap-4">
      <div class="bg-gray-900/40 rounded-lg ring-1 ring-gray-700 p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Linha vinculada</div>
        <div class="mt-2 text-gray-100">${line.nome_visivel || line.numero_ramal || '—'}</div>
        <div class="text-xs text-gray-500 mt-1">
          Ramal <span class="font-mono text-gray-300">${line.numero_ramal || '—'}</span> ·
          IP <span class="font-mono text-gray-300">${line.ip || '—'}</span>
        </div>
        <div class="text-xs text-gray-500 mt-1">
          Ambiente <a href="/extension-configurator/environments/${line.environment_id}" class="text-blue-400 hover:underline">${line.environment_nome}</a>
          · ${line.modelo_telefone}
        </div>
      </div>
      <div class="bg-gray-900/40 rounded-lg ring-1 ring-gray-700 p-4">
        <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Última config aplicada</div>
        <div class="mt-2">${statusBadge(line.ultimo_status)}</div>
        <div class="text-xs text-gray-500 mt-2">${ts}</div>
        <div class="text-xs text-gray-500 mt-1">Hash: <span class="font-mono">${(line.ultimo_hash_aplicado || '—').slice(0, 12)}</span></div>
        ${erroLine}
      </div>
    </div>
    ${eventsHTML}`;
  injectIcons();

  document.querySelector('[data-action="apply-config"]')?.addEventListener('click', applyConfig);
  document.querySelector('[data-action="unlink"]')?.addEventListener('click', unlinkDevice);
}

async function loadEC() {
  try {
    const [linked, events] = await Promise.all([
      api(`/api/devices/${id}/extension-line`),
      api(`/api/devices/${id}/reapply-events?limit=10`),
    ]);
    if (!linked) {
      renderLinkedEmpty();
    } else {
      renderLinked(linked, events || []);
    }
  } catch (_e) {
    document.getElementById('ec-body').innerHTML = `<div class="text-xs text-red-300">Falha ao carregar vínculo.</div>`;
  }
}

async function applyConfig(e) {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    const r = await api(`/api/devices/${id}/apply-config`, { method: 'POST' });
    toast.success(`Apply disparado (run ${r.run_id})`);
    setTimeout(loadEC, 3000);
  } catch (err) {
    toast.error('Falha ao disparar apply');
  } finally {
    btn.disabled = false;
  }
}

async function unlinkDevice(e) {
  if (!confirm('Desvincular este ramal da linha? O ramal continuará no ambiente mas não será mais auto-reconfigurado.')) return;
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    await api(`/api/devices/${id}/link`, { method: 'DELETE' });
    toast.success('Desvinculado');
    loadEC();
  } catch (err) {
    toast.error('Falha ao desvincular');
  } finally {
    btn.disabled = false;
  }
}

async function openLinkModal() {
  document.getElementById('ec-link-modal').classList.remove('hidden');
  showLinkStep('env');
  try {
    const envs = await api(`/api/devices/${id}/link-environments`);
    renderLinkEnvs(envs);
  } catch (e) {
    document.getElementById('ec-link-envs').innerHTML = `<div class="p-4 text-xs text-red-300">Falha ao carregar ambientes.</div>`;
  }
}

function showLinkStep(step) {
  document.getElementById('ec-link-step-env').classList.toggle('hidden', step !== 'env');
  document.getElementById('ec-link-step-line').classList.toggle('hidden', step !== 'line');
}

function renderLinkEnvs(envs) {
  if (!envs.length) {
    document.getElementById('ec-link-envs').innerHTML = `
      <div class="p-4 text-xs text-gray-400">
        Nenhum ambiente com linhas disponíveis. Cadastre o ramal em um ambiente primeiro.
      </div>`;
    return;
  }
  document.getElementById('ec-link-envs').innerHTML = envs.map((e) => `
    <div class="px-3 py-2.5 hover:bg-gray-800/60 flex items-center justify-between gap-3">
      <div class="min-w-0">
        <div class="text-sm text-gray-100 flex items-center gap-2">
          ${e.environment_nome}
          ${e.has_match ? '<span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-green-500/15 text-green-400 ring-1 ring-inset ring-green-500/30">IP bate</span>' : ''}
        </div>
        <div class="text-xs text-gray-500">${e.modelo_telefone} · ${e.orphan_lines} linha(s) sem device</div>
      </div>
      <button data-env="${e.environment_id}" class="inline-flex items-center gap-1 rounded-lg font-medium bg-blue-500 hover:bg-blue-400 text-white px-2.5 py-1 text-xs">Selecionar</button>
    </div>`).join('');
  document.getElementById('ec-link-envs').querySelectorAll('[data-env]').forEach((b) => {
    b.addEventListener('click', () => pickEnvironment(b.dataset.env, b.parentElement.querySelector('.text-sm').textContent.trim()));
  });
}

async function pickEnvironment(env_id, env_label) {
  showLinkStep('line');
  document.getElementById('ec-link-env-label').textContent = env_label;
  document.getElementById('ec-link-suggestion').innerHTML = '<div class="text-xs text-gray-400">Buscando sugestão…</div>';
  document.getElementById('ec-link-candidates').innerHTML = '';
  try {
    const r = await api(`/api/devices/${id}/link-suggestion?environment_id=${encodeURIComponent(env_id)}`);
    renderSuggestion(r);
  } catch (e) {
    document.getElementById('ec-link-suggestion').innerHTML = `<div class="text-xs text-red-300">Falha ao consultar sugestão.</div>`;
  }
}

function reasonLabel(r) {
  if (r === 'ip_match') return 'IP igual';
  if (r === 'ramal_match') return 'ramal igual';
  if (r === 'single_orphan') return 'única linha do ambiente';
  return r;
}

function renderSuggestion(r) {
  const wrap = document.getElementById('ec-link-suggestion');
  if (r.auto_resolved && r.line) {
    const l = r.line;
    wrap.innerHTML = `
      <div class="rounded-lg ring-1 ring-blue-500/30 bg-blue-500/5 p-4">
        <div class="text-[11px] uppercase tracking-wider text-blue-300 font-semibold mb-1">Sugestão automática (${reasonLabel(r.reason)})</div>
        <div class="text-sm text-gray-100">${l.nome_visivel || l.numero_ramal} <span class="text-gray-500 font-mono text-xs ml-1">${l.ip || 's/IP'}</span></div>
        <div class="text-xs text-gray-500 mt-0.5">ramal ${l.numero_ramal || '—'}</div>
        <div class="mt-3 flex items-center gap-2">
          <button data-link="${l.line_id}" class="inline-flex items-center gap-1 rounded-lg font-medium bg-blue-500 hover:bg-blue-400 text-white px-3 py-1.5 text-xs">Vincular esta linha</button>
          <button data-action="link-show-all" class="inline-flex items-center gap-1 rounded-lg font-medium bg-transparent hover:bg-gray-700/60 text-gray-300 ring-1 ring-inset ring-gray-700 px-3 py-1.5 text-xs">Escolher outra…</button>
        </div>
      </div>`;
    wrap.querySelector('[data-link]')?.addEventListener('click', () => confirmLink(l.line_id));
    wrap.querySelector('[data-action="link-show-all"]')?.addEventListener('click', () => renderCandidatesList(r.candidates));
    return;
  }
  wrap.innerHTML = `
    <div class="rounded-lg ring-1 ring-gray-700 bg-gray-900/40 p-3 text-xs text-gray-400">
      ${r.reason === 'none'
        ? 'Não há linhas órfãs neste ambiente.'
        : 'Nenhuma sugestão automática (IP/ramal não casaram com nenhuma linha única). Escolha manualmente abaixo.'}
    </div>`;
  renderCandidatesList(r.candidates);
}

function renderCandidatesList(candidates) {
  const cont = document.getElementById('ec-link-candidates');
  if (!candidates || !candidates.length) {
    cont.innerHTML = `<div class="mt-3 text-xs text-gray-500">Sem candidatas neste ambiente.</div>`;
    return;
  }
  cont.innerHTML = `
    <div class="mt-3">
      <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold mb-2">Linhas do ambiente (sem device vinculado)</div>
      <div class="max-h-60 overflow-y-auto divide-y divide-gray-800 ring-1 ring-gray-800 rounded-lg">
        ${candidates.map((l) => `
          <div class="px-3 py-2 hover:bg-gray-800/60 flex items-center justify-between gap-3">
            <div class="min-w-0">
              <div class="text-sm text-gray-100">${l.nome_visivel || l.numero_ramal} <span class="text-gray-500 font-mono text-xs ml-1">${l.ip || 's/IP'}</span></div>
              <div class="text-xs text-gray-500">ramal ${l.numero_ramal || '—'}</div>
            </div>
            <button data-link="${l.line_id}" class="inline-flex items-center gap-1 rounded-lg font-medium bg-blue-500 hover:bg-blue-400 text-white px-2.5 py-1 text-xs">Vincular</button>
          </div>`).join('')}
      </div>
    </div>`;
  cont.querySelectorAll('[data-link]').forEach((b) => {
    b.addEventListener('click', () => confirmLink(b.dataset.link));
  });
}

async function confirmLink(line_id) {
  try {
    await api(`/api/devices/${id}/link`, { method: 'POST', body: { line_id } });
    toast.success('Vinculado');
    closeLinkModal();
    loadEC();
  } catch (e) {
    toast.error(e?.body?.detail || 'Falha ao vincular');
  }
}

function closeLinkModal() {
  document.getElementById('ec-link-modal').classList.add('hidden');
}

document.querySelectorAll('[data-action="link-close"]').forEach((b) => b.addEventListener('click', closeLinkModal));
document.querySelector('[data-action="link-back"]')?.addEventListener('click', () => showLinkStep('env'));
document.getElementById('ec-link-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'ec-link-modal') closeLinkModal();
});

load();
loadEC();
setInterval(load, 10000);
setInterval(loadEC, 15000);
