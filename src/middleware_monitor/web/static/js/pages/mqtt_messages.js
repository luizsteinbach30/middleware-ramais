// Consulta do ledger de mensagens MQTT.
//
// A tela existe para uma pergunta: "essa mensagem foi publicada?". Por isso o
// período vem primeiro, o payload cru é mostrado como chegou, e a faixa de
// cobertura acompanha o resultado — lista vazia só significa alguma coisa se o
// coletor estava ouvindo no período.

import { api, qs } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

const PAGE = 100;
const LIVE_MS = 3000;

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// A tela também é destino de link: o painel ao vivo manda para cá com o ramal
// já preenchido ("quero ver as mensagens cruas deste ramal"), e nesse caso a
// janela padrão é maior — quem chega assim está investigando, não vigiando.
const parametros = new URLSearchParams(window.location.search);
const ramalInicial = (parametros.get('ramal') || '').trim().slice(0, 64);

const state = {
  last: parametros.get('last') || (ramalInicial ? '1h' : '15m'),
  since: '', until: '',
  topic: '', ramal: ramalInicial, contains: '', pinned: false,
  live: false,
};

let linhas = [];        // o que está na tabela, mais recente primeiro
let detalhe = null;     // mensagem aberta no modal
let abaAtual = 'pretty';
let liveTimer = null;

// ── consulta ────────────────────────────────────────────────────────────────

function filtros() {
  const f = {
    topic: state.topic || undefined,
    ramal: state.ramal || undefined,
    contains: state.contains || undefined,
    pinned: state.pinned || undefined,
  };
  if (state.last) {
    f.last = state.last;
  } else {
    // datetime-local é hora local; o backend fala UTC.
    if (state.since) f.since = new Date(state.since).toISOString();
    if (state.until) f.until = new Date(state.until).toISOString();
  }
  return f;
}

async function buscar() {
  const tbody = el('mm-tbody');
  tbody.innerHTML = `<tr><td colspan="5" class="px-4 py-10 text-center text-sm text-gray-500">Buscando…</td></tr>`;
  try {
    const dados = await api('/api/mqtt/messages' + qs({ ...filtros(), limit: PAGE }));
    linhas = dados.items;
    render(dados);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="px-4 py-10 text-center text-sm text-red-300">${esc(traduzErro(e))}</td></tr>`;
    el('mm-count').textContent = '—';
  }
  carregarCobertura();
}

function traduzErro(e) {
  const d = String(e.message || '');
  if (d.startsWith('topic_invalid:')) {
    const motivos = {
      filtro_vazio: 'informe o tópico ou deixe em branco',
      curinga_multinivel_no_fim: 'o "#" só pode aparecer no fim do filtro',
      curinga_multinivel_isolado: 'o "#" tem de ocupar um nível inteiro (ex.: v1/data/#)',
      curinga_um_nivel_isolado: 'o "+" tem de ocupar um nível inteiro (ex.: v1/data/+)',
      filtro_muito_longo: 'filtro longo demais',
    };
    return `Filtro de tópico inválido: ${motivos[d.split(':')[1]] || d.split(':')[1]}`;
  }
  if (d === 'invalid_span') return 'Período inválido.';
  return `Falha na busca: ${d}`;
}

async function carregarMais() {
  if (!linhas.length) return;
  const btn = el('mm-more');
  btn.disabled = true;
  try {
    const dados = await api('/api/mqtt/messages' + qs({
      ...filtros(), limit: PAGE, before_id: linhas[linhas.length - 1].id,
    }));
    linhas = linhas.concat(dados.items);
    render({ ...dados, total: dados.total });
  } catch (e) {
    toast.error(traduzErro(e));
  } finally {
    btn.disabled = false;
  }
}

async function puxarNovas() {
  if (!linhas.length) { await buscar(); return; }
  try {
    const dados = await api('/api/mqtt/messages' + qs({
      ...filtros(), limit: 500, after_id: linhas[0].id,
    }));
    if (!dados.items.length) return;
    // O endpoint devolve as novas em ordem cronológica; a tabela é ao contrário.
    linhas = dados.items.slice().reverse().concat(linhas);
    render({ total: linhas.length, exact_total: true, truncated: false }, { destacar: dados.items.length });
  } catch (_e) { /* silencioso: o modo ao vivo não pode encher a tela de erro */ }
}

// ── tabela ──────────────────────────────────────────────────────────────────

function render(dados, { destacar = 0 } = {}) {
  const tbody = el('mm-tbody');
  if (!linhas.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="px-4 py-10 text-center text-sm text-gray-500">Nenhuma mensagem no período com esses filtros.</td></tr>`;
    el('mm-count').textContent = '0 mensagens';
    el('mm-more').classList.add('hidden');
    return;
  }
  tbody.innerHTML = linhas.map((m, i) => `
    <tr class="hover:bg-gray-700/30 cursor-pointer ${i < destacar ? 'bg-blue-500/5' : ''}" data-open="${m.id}">
      <td class="px-4 py-2 font-mono text-xs text-gray-300 whitespace-nowrap">${fmtTs(m.received_at)}</td>
      <td class="px-4 py-2 font-mono text-xs text-gray-400 truncate max-w-[22rem]" title="${esc(m.topic)}">${esc(m.topic)}</td>
      <td class="px-4 py-2 font-mono text-xs text-gray-300">${esc(m.ramal || '—')}</td>
      <td class="px-4 py-2 text-xs text-gray-400 truncate max-w-[26rem]">${m.pinned ? '<span class="mr-1.5 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30">evidência</span>' : ''}${esc(m.preview)}</td>
      <td class="px-4 py-2 text-right whitespace-nowrap">
        <div class="inline-flex gap-1">
          <button data-open="${m.id}" class="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100" title="Ver mensagem"><span data-icon="eye"></span></button>
          <a href="/api/mqtt/messages/${m.id}/comprovante" class="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100 inline-block" title="Baixar comprovante"><span data-icon="download"></span></a>
        </div>
      </td>
    </tr>`).join('');
  tbody.querySelectorAll('[data-open]').forEach((n) => n.addEventListener('click', (ev) => {
    ev.stopPropagation();
    abrir(+n.dataset.open);
  }));

  const total = dados.exact_total === false ? `mais de ${dados.total}` : dados.total;
  el('mm-count').textContent = `${linhas.length} exibidas de ${total} no período`;
  el('mm-more').classList.toggle('hidden', linhas.length >= dados.total && dados.exact_total !== false);
  injectIcons();
}

// ── cobertura ───────────────────────────────────────────────────────────────

function fmtDuracao(seg) {
  if (seg < 60) return `${seg}s`;
  if (seg < 3600) return `${Math.round(seg / 60)} min`;
  return `${(seg / 3600).toFixed(1).replace('.', ',')} h`;
}

async function carregarCobertura() {
  const box = el('mm-coverage');
  try {
    const c = await api('/api/mqtt/coverage' + qs(state.last ? { last: state.last } : {
      since: state.since ? new Date(state.since).toISOString() : undefined,
      until: state.until ? new Date(state.until).toISOString() : undefined,
    }));
    if (c.unknown && !c.covered_seconds) {
      box.className = 'rounded-xl px-4 py-3 text-xs ring-1 ring-inset bg-amber-500/10 ring-amber-500/30 text-amber-200';
      box.innerHTML = 'Sem histórico do coletor neste período — a ausência de mensagens aqui <strong>não prova</strong> que nada foi publicado.';
      return;
    }
    if (c.coverage_pct >= 99.9) {
      box.className = 'rounded-xl px-4 py-3 text-xs ring-1 ring-inset bg-green-500/10 ring-green-500/30 text-green-300';
      box.innerHTML = 'Coletor conectado durante <strong>100% do período</strong> — o que não está aqui não foi publicado.';
      return;
    }
    box.className = 'rounded-xl px-4 py-3 text-xs ring-1 ring-inset bg-amber-500/10 ring-amber-500/30 text-amber-200';
    const lacunas = c.gaps.slice(0, 4).map((g) => `<li>${fmtTs(g.started_at)} · ${fmtDuracao(g.seconds)} — ${esc(g.detail)}</li>`).join('');
    box.innerHTML = `Coletor ouvindo em <strong>${String(c.coverage_pct).replace('.', ',')}%</strong> do período.
      <ul class="mt-1.5 space-y-0.5 list-disc list-inside opacity-90">${lacunas}</ul>
      ${c.gaps.length > 4 ? `<div class="mt-1 opacity-75">e mais ${c.gaps.length - 4} lacuna(s).</div>` : ''}`;
  } catch (_e) {
    box.className = 'rounded-xl px-4 py-3 text-xs ring-1 ring-inset bg-gray-800 ring-gray-700 text-gray-500';
    box.textContent = 'Não foi possível verificar a cobertura do período.';
  }
}

async function atualizarColetor() {
  try {
    const s = await api('/api/mqtt/status');
    const pill = el('mm-collector');
    if (!s.configured) {
      pill.className = 'px-2.5 py-1 rounded-full text-[11px] font-medium bg-gray-600/30 text-gray-400 ring-1 ring-inset ring-gray-600 whitespace-nowrap';
      pill.innerHTML = 'coletor: não configurado — <a class="underline" href="/config">configurar</a>';
      return;
    }
    const b = s.brokers[0] || {};
    const ligado = b.state === 'connected' || b.state === 'subscribed';
    pill.className = `px-2.5 py-1 rounded-full text-[11px] font-medium ring-1 ring-inset whitespace-nowrap ${ligado ? 'bg-green-500/15 text-green-300 ring-green-500/30' : 'bg-red-500/15 text-red-300 ring-red-500/30'}`;
    pill.textContent = ligado ? `coletando · ${s.per_minute} msg/min` : 'coletor sem conexão';
    // Sugere os tópicos que estão sendo gravados no campo de filtro.
    const dl = el('mm-topic-sugestoes');
    if (dl && !dl.childElementCount) {
      dl.innerHTML = (b.topics || []).map((t) => `<option value="${esc(t)}"></option>`).join('');
    }
  } catch (_e) { /* barra de estado é acessório */ }
}

// ── detalhe ─────────────────────────────────────────────────────────────────

async function abrir(id) {
  try {
    detalhe = await api('/api/mqtt/messages/' + id);
  } catch (_e) { toast.error('Falha ao abrir a mensagem'); return; }
  const m = detalhe;
  el('mm-modal-meta').innerHTML = [
    `registro #${m.id}`,
    `recebida ${fmtTs(m.received_at)}`,
    m.event_at ? `evento no PBX ${fmtTs(m.event_at)}` : '',
    `tópico ${esc(m.topic)}`,
    `QoS ${m.qos}${m.retained ? ' · retained' : ''}${m.b64 ? ' · binário (base64)' : ''}${m.truncated ? ' · truncada' : ''}`,
    m.broker ? `broker ${esc(m.broker)}` : '',
  ].filter(Boolean).map((l) => `<div>${l}</div>`).join('');
  abaAtual = m.pretty ? 'pretty' : 'raw';
  aplicarAba();
  atualizarBotaoPin();
  el('mm-modal').classList.remove('hidden');
  injectIcons();
}

function aplicarAba() {
  const m = detalhe;
  if (!m) return;
  el('mm-modal-content').textContent = abaAtual === 'pretty' ? (m.pretty || m.payload) : m.payload;
  document.querySelectorAll('#mm-modal [data-tab]').forEach((b) => {
    const ativo = b.dataset.tab === abaAtual;
    b.className = ativo
      ? 'px-2.5 py-1 rounded-md text-xs font-medium bg-blue-500/10 text-blue-300 ring-1 ring-inset ring-blue-500/30'
      : 'px-2.5 py-1 rounded-md text-xs font-medium text-gray-400 hover:text-gray-100';
    b.disabled = b.dataset.tab === 'pretty' && !m.pretty;
    if (b.disabled) b.classList.add('opacity-40');
  });
}

function atualizarBotaoPin() {
  el('mm-modal-pin-label').textContent = detalhe?.pinned ? 'Soltar evidência' : 'Fixar evidência';
  el('mm-modal-pin').classList.toggle('ring-amber-500/40', !!detalhe?.pinned);
  el('mm-modal-pin').classList.toggle('text-amber-300', !!detalhe?.pinned);
}

async function alternarPin() {
  if (!detalhe) return;
  const novo = !detalhe.pinned;
  try {
    await api(`/api/mqtt/messages/${detalhe.id}/pin`, { method: 'POST', body: { pinned: novo } });
    detalhe.pinned = novo;
    const linha = linhas.find((l) => l.id === detalhe.id);
    if (linha) linha.pinned = novo;
    atualizarBotaoPin();
    render({ total: linhas.length, exact_total: true });
    toast.success(novo ? 'Fixada como evidência — a retenção não apaga.' : 'Evidência solta.');
  } catch (e) { toast.error(`Falha ao fixar: ${e.message}`); }
}

// ── controles ───────────────────────────────────────────────────────────────

function marcarPreset() {
  document.querySelectorAll('#mm-presets [data-last]').forEach((b) => {
    const ativo = b.dataset.last === state.last;
    b.classList.toggle('bg-blue-500/15', ativo);
    b.classList.toggle('text-blue-300', ativo);
    b.classList.toggle('text-gray-300', !ativo);
  });
  el('mm-range').classList.toggle('hidden', !!state.last);
}

function ligarLive(on) {
  state.live = on;
  el('mm-live-dot').className = `w-1.5 h-1.5 rounded-full ${on ? 'bg-green-400 animate-pulse' : 'bg-gray-500'}`;
  el('mm-live-label').textContent = on ? 'Ao vivo (ligado)' : 'Ao vivo';
  el('mm-live').classList.toggle('ring-green-500/40', on);
  clearInterval(liveTimer);
  if (on) liveTimer = setInterval(puxarNovas, LIVE_MS);
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// 250 ms: rápido o bastante para parecer que filtra enquanto se digita,
// longo o bastante para não disparar uma consulta por tecla.
const buscarComAtraso = debounce(buscar, 250);

if (el('mm-tbody')) {
  document.querySelectorAll('#mm-presets [data-last]').forEach((b) => b.addEventListener('click', () => {
    state.last = b.dataset.last;
    marcarPreset();
    if (state.last) buscar();
  }));
  for (const [id, campo] of [['mm-topic', 'topic'], ['mm-ramal', 'ramal'], ['mm-contains', 'contains']]) {
    el(id).addEventListener('input', () => { state[campo] = el(id).value.trim(); buscarComAtraso(); });
  }
  el('mm-pinned').addEventListener('change', () => { state.pinned = el('mm-pinned').checked; buscar(); });
  for (const id of ['mm-since', 'mm-until']) {
    el(id).addEventListener('change', () => {
      state.since = el('mm-since').value;
      state.until = el('mm-until').value;
      if (!state.last) buscar();
    });
  }
  el('mm-clear').addEventListener('click', () => {
    Object.assign(state, { topic: '', ramal: '', contains: '', pinned: false, last: '15m' });
    el('mm-topic').value = ''; el('mm-ramal').value = ''; el('mm-contains').value = '';
    el('mm-pinned').checked = false;
    marcarPreset();
    buscar();
  });
  el('mm-refresh').addEventListener('click', buscar);
  el('mm-more').addEventListener('click', carregarMais);
  el('mm-live').addEventListener('click', () => ligarLive(!state.live));

  el('mm-modal').addEventListener('click', (ev) => {
    if (ev.target.id === 'mm-modal' || ev.target.hasAttribute('data-close')) {
      el('mm-modal').classList.add('hidden');
    }
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') el('mm-modal').classList.add('hidden');
  });
  document.querySelectorAll('#mm-modal [data-tab]').forEach((b) => b.addEventListener('click', () => {
    abaAtual = b.dataset.tab;
    aplicarAba();
  }));
  el('mm-modal-pin').addEventListener('click', alternarPin);
  el('mm-modal-copy').addEventListener('click', async () => {
    if (!detalhe) return;
    await navigator.clipboard.writeText(el('mm-modal-content').textContent || '');
    toast.success('Conteúdo copiado.');
  });
  el('mm-modal-download').addEventListener('click', () => {
    if (detalhe) window.location.href = `/api/mqtt/messages/${detalhe.id}/comprovante`;
  });

  el('mm-ramal').value = state.ramal;
  marcarPreset();
  buscar();
  atualizarColetor();
  setInterval(atualizarColetor, 10000);
}
