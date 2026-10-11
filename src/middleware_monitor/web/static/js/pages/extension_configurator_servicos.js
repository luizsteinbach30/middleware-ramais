// Serviços do ambiente (Telefone, DVR, Câmeras) e as abas DVR/Câmeras — NOC 1.4.0.
//
// A aba Telefones é a planilha de sempre (extension_configurator_detail.js). Este
// módulo só liga/desliga os serviços, troca as abas e cuida das planilhas de DVR e
// câmeras, que gravam na lista de equipamentos do agente (o mesmo caminho do NOC).
// Nada daqui fala com o DVR nem com a câmera.
import { toast } from '/static/js/components/toast.js';

const envId = window.EC_ENV_ID;
const BASE = `/api/extension-configurator/environments/${encodeURIComponent(envId)}`;
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const ROTULO = { telefone: 'Telefones', dvr: 'DVR', camera: 'Câmeras' };
const ESTADO = { no_ar: '● no ar', fora: '● fora', degradado: '● degradado', pausada: '‖ pausada', desconhecido: '○ aguardando' };

const COLUNAS = {
  dvr: [
    { type: 'hidden', name: 'id' },
    { type: 'hidden', name: 'ref' },
    { type: 'text', name: 'nome', title: 'Nome', width: 180 },
    { type: 'text', name: 'ip', title: 'IP', width: 130 },
    { type: 'numeric', name: 'portaHttp', title: 'Porta HTTP', width: 90 },
    { type: 'numeric', name: 'portaRtsp', title: 'Porta RTSP', width: 90 },
    { type: 'dropdown', name: 'adaptador', title: 'Adaptador', width: 110, source: [] },
    { type: 'text', name: 'modelo', title: 'Modelo', width: 120 },
    { type: 'text', name: 'usuario', title: 'Usuário', width: 100 },
    { type: 'text', name: 'senha', title: 'Senha', width: 110 },
    { type: 'numeric', name: 'canais', title: 'Nº canais', width: 80 },
    { type: 'text', name: '_senha', title: 'Senha guardada', width: 110, readOnly: true },
    { type: 'text', name: '_lidos', title: 'Canais lidos', width: 90, readOnly: true },
    { type: 'text', name: '_estado', title: 'Estado', width: 120, readOnly: true },
    { type: 'text', name: '_cftv', title: 'Leitura CFTV', width: 120, readOnly: true },
  ],
  camera: [
    { type: 'hidden', name: 'id' },
    { type: 'hidden', name: 'ref' },
    { type: 'text', name: 'nome', title: 'Nome', width: 180 },
    { type: 'text', name: 'ip', title: 'IP', width: 130 },
    { type: 'dropdown', name: 'dvr', title: 'DVR do ambiente', width: 180, source: [] },
    { type: 'numeric', name: 'canal', title: 'Canal', width: 70 },
    { type: 'text', name: 'fabricante', title: 'Fabricante', width: 120 },
    { type: 'text', name: 'modelo', title: 'Modelo', width: 120 },
    { type: 'text', name: '_estado', title: 'Estado', width: 120, readOnly: true },
  ],
};
const EDITAVEIS = {
  dvr: ['nome', 'ip', 'portaHttp', 'portaRtsp', 'adaptador', 'modelo', 'usuario', 'senha', 'canais'],
  camera: ['nome', 'ip', 'dvr', 'canal', 'fabricante', 'modelo'],
};
const AJUDA = {
  dvr: 'Uma linha por gravador. Porta RTSP em branco = sem verificar o vídeo. A senha nunca é mostrada: em branco mantém a guardada. O adaptador só lista os módulos de CFTV aceitos neste agente.',
  camera: 'Uma linha por câmera. Escolha o DVR do ambiente em que ela grava e o canal. Para ligar a um DVR novo, salve o DVR antes ou junto (o salvar grava as duas abas).',
};

const estado = {
  servicos: [],
  modelo: '',
  telefones: 0,
  adaptadores: [],
  dados: { dvr: [], camera: [] },  // linhas como objetos, inclusive as não salvas
  aba: null,
  sujo: false,
};
let sheet = null;

async function pedir(caminho, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRF-Token'] = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const res = await fetch(caminho, {
    method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); throw new Error('unauthorized'); }
  const corpo = await res.json().catch(() => null);
  if (!res.ok) {
    const d = corpo && corpo.detail;
    const erro = new Error(typeof d === 'string' ? d : (d && d.mensagem) || res.statusText);
    erro.detalhe = d && typeof d === 'object' ? d : null;
    erro.status = res.status;
    throw erro;
  }
  return corpo;
}

function novoRef() {
  const b = new Uint8Array(6);
  crypto.getRandomValues(b);
  return 'mw_' + Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}

// --- linhas <-> planilha -----------------------------------------------------------------

function paraLinha(aba, l) {
  if (aba === 'dvr') {
    return {
      ...l,
      portaHttp: l.portaHttp ?? '', portaRtsp: l.portaRtsp ?? '', canais: l.canais ?? '',
      _senha: l.senhaGuardada ? '•••• guardada' : '—',
      _lidos: l.canaisLidos ?? '—',
      _estado: ESTADO[l.estado] || l.estado || '',
      _cftv: l.verificacaoCftv ? (ESTADO[l.verificacaoCftv] || l.verificacaoCftv) : '—',
    };
  }
  return { ...l, canal: l.canal ?? '', _estado: ESTADO[l.estado] || l.estado || '' };
}

function linhaVazia(aba, l) {
  return EDITAVEIS[aba].every((c) => String(l[c] ?? '').trim() === '');
}

function lerPlanilha() {
  if (!sheet || !estado.aba || estado.aba === 'telefone') return;
  const cols = COLUNAS[estado.aba];
  const antigas = new Map(estado.dados[estado.aba].filter((l) => l.id).map((l) => [l.id, l]));
  estado.dados[estado.aba] = sheet.getData().map((row) => {
    const l = Object.fromEntries(cols.map((c, i) => [c.name, row[i]]));
    // As colunas só de leitura voltam do que veio do servidor.
    const antes = l.id ? antigas.get(l.id) : null;
    return antes ? { ...antes, ...Object.fromEntries(EDITAVEIS[estado.aba].map((c) => [c, l[c]])), ref: l.ref } : l;
  });
}

function fonteDeDvr() {
  // As câmeras escolhem pelo ref; DVR novo ganha o ref aqui (é o que o servidor aceita).
  return estado.dados.dvr
    .filter((l) => !linhaVazia('dvr', l))
    .map((l) => {
      if (!l.ref) l.ref = novoRef();
      return { id: l.ref, name: l.nome || l.ip || l.ref };
    });
}

function destruir() {
  const el = $('#ec-cftv-sheet');
  if (sheet) {
    try { jspreadsheet.destroy(el, true); } catch (_e) { /* versão sem destroy estático */ }
  }
  sheet = null;
  el.innerHTML = '';
}

function montarPlanilha(aba) {
  destruir();
  const cols = COLUNAS[aba].map((c) => ({ ...c }));
  if (aba === 'dvr') cols.find((c) => c.name === 'adaptador').source = estado.adaptadores;
  // Sem DVR = célula vazia (Delete limpa); uma opção "sem DVR" pintaria todas as linhas vazias.
  if (aba === 'camera') cols.find((c) => c.name === 'dvr').source = fonteDeDvr();
  const data = estado.dados[aba].map((l) => cols.map((c) => paraLinha(aba, l)[c.name] ?? ''));
  while (data.length < Math.max(estado.dados[aba].length + 10, 20)) data.push(cols.map(() => ''));
  const editaveis = new Set(EDITAVEIS[aba].map((n) => cols.findIndex((c) => c.name === n)));
  sheet = jspreadsheet($('#ec-cftv-sheet'), {
    data,
    columns: cols,
    tableOverflow: true,
    tableWidth: '100%',
    tableHeight: 'calc(100vh - 320px)',
    allowInsertColumn: false,
    allowDeleteColumn: false,
    columnDrag: false,
    defaultColAlign: 'left',
    onafterchanges: (_i, records) => {
      if ((records || []).some((r) => editaveis.has(parseInt(r.x, 10)))) marcarSujo(true);
    },
  });
}

// --- serviços e abas ---------------------------------------------------------------------

function marcarSujo(sujo) {
  estado.sujo = sujo;
  $('#ec-cftv-status').textContent = sujo ? 'não salvo' : '';
  $('#ec-cftv-status').className = `text-[11px] font-medium self-center ${sujo ? 'text-amber-300' : 'text-gray-500'}`;
}

function renderServicos() {
  for (const b of $$('#ec-servicos [data-servico]')) {
    b.setAttribute('aria-pressed', String(estado.servicos.includes(b.dataset.servico)));
  }
  for (const t of $$('#ec-abas [data-aba]')) {
    t.classList.toggle('hidden', !estado.servicos.includes(t.dataset.aba));
  }
  const conta = { telefone: estado.telefones, dvr: estado.dados.dvr.filter((l) => l.id).length, camera: estado.dados.camera.filter((l) => l.id).length };
  for (const s of $$('#ec-abas [data-conta]')) s.textContent = conta[s.dataset.conta] ?? '';
  const sub = $('#ec-subtitle');
  if (sub && !estado.servicos.includes('telefone')) {
    sub.textContent = estado.servicos.map((s) => `${ROTULO[s]} ${conta[s]}`).join(' · ');
  }
}

function abrirAba(aba) {
  if (!estado.servicos.includes(aba)) aba = estado.servicos[0];
  if (estado.aba && estado.aba !== 'telefone') lerPlanilha();
  estado.aba = aba;
  for (const t of $$('#ec-abas [data-aba]')) t.setAttribute('aria-selected', String(t.dataset.aba === aba));
  $('#ec-aba-telefone').classList.toggle('hidden', aba !== 'telefone');
  $('#ec-aba-cftv').classList.toggle('hidden', aba === 'telefone');
  if (aba !== 'telefone') {
    $('#ec-cftv-rotulo').textContent = ROTULO[aba];
    $('#ec-cftv-ajuda').textContent = AJUDA[aba];
    const aviso = $('#ec-cftv-aviso');
    const semAdaptador = aba === 'dvr' && estado.adaptadores.length === 0;
    aviso.classList.toggle('hidden', !semAdaptador);
    aviso.innerHTML = semAdaptador
      ? 'Nenhum módulo de CFTV está aceito neste agente: o adaptador fica vazio. Ligue o do fabricante em <a href="/system/modulos" class="underline hover:text-amber-100">Sistema → Módulos</a>.'
      : '';
    montarPlanilha(aba);
  }
  if (location.hash.slice(1) !== aba) history.replaceState(null, '', `#${aba}`);
}

async function carregar() {
  const [env, eqs] = await Promise.all([pedir(BASE), pedir(`${BASE}/equipamentos`)]);
  estado.servicos = env.servicos || ['telefone'];
  estado.modelo = env.modelo_telefone || '';
  estado.telefones = (env.linhas || []).length;
  aplicarEquipamentos(eqs);
}

function aplicarEquipamentos(eqs) {
  estado.adaptadores = eqs.adaptadores || [];
  estado.servicos = eqs.servicos || estado.servicos;
  estado.dados = { dvr: eqs.dvr || [], camera: eqs.camera || [] };
  marcarSujo(false);
  renderServicos();
}

async function escolherModelo() {
  const { models } = await pedir('/api/extension-configurator/phone-models');
  const sel = $('#ec-modelo-select');
  sel.innerHTML = models.map((m) => `<option>${m.replace(/</g, '&lt;')}</option>`).join('');
  const modal = $('#ec-modelo-modal');
  modal.classList.remove('hidden');
  return new Promise((ok) => {
    const fechar = (valor) => { modal.classList.add('hidden'); ok(valor); };
    $('#ec-modelo-cancel').onclick = () => fechar(null);
    $('#ec-modelo-confirm').onclick = () => fechar(sel.value);
  });
}

async function alternarServico(servico, botao) {
  const ligado = estado.servicos.includes(servico);
  const novos = ligado ? estado.servicos.filter((s) => s !== servico) : [...estado.servicos, servico];
  if (novos.length === 0) { toast.warn('O ambiente precisa de pelo menos um serviço.'); return; }
  if (ligado && servico !== 'telefone' && estado.dados[servico].some((l) => l.id)
      && !confirm(`Desligar ${ROTULO[servico]}? Os equipamentos ficam guardados e as verificações pausam até religar.`)) return;
  const body = { servicos: novos };
  if (!ligado && servico === 'telefone' && !estado.modelo) {
    const modelo = await escolherModelo();
    if (!modelo) return;
    body.modelo_telefone = modelo;
  }
  botao.disabled = true;
  try {
    const r = await pedir(`${BASE}/servicos`, { method: 'PUT', body });
    estado.servicos = r.servicos;
    estado.modelo = r.modelo_telefone || '';
    renderServicos();
    toast.success(`${ROTULO[servico]} ${ligado ? 'desligado' : 'ligado'}.`);
    if (servico === 'telefone' && !ligado) { location.hash = 'telefone'; location.reload(); return; }
    abrirAba(ligado && estado.aba === servico ? estado.servicos[0] : (ligado ? estado.aba : servico));
  } catch (e) {
    toast.error(e.message);
  } finally {
    botao.disabled = false;
  }
}

async function salvar() {
  lerPlanilha();
  const pedido = {};
  for (const aba of ['dvr', 'camera']) {
    if (!estado.servicos.includes(aba)) continue;
    pedido[aba] = estado.dados[aba]
      .filter((l) => !linhaVazia(aba, l))
      .map((l) => {
        const saida = { id: l.id || null, ref: l.ref || (aba === 'dvr' ? novoRef() : null) };
        for (const c of EDITAVEIS[aba]) saida[c] = typeof l[c] === 'string' ? l[c].trim() : l[c];
        return saida;
      });
  }
  const botao = $('#ec-cftv-save');
  botao.disabled = true;
  try {
    const r = await pedir(`${BASE}/equipamentos`, { method: 'PUT', body: pedido });
    aplicarEquipamentos(r);
    abrirAba(estado.aba);
    toast.success('Equipamentos salvos.');
  } catch (e) {
    toast.error(e.message);
    const d = e.detalhe;
    if (d && d.aba && d.aba !== estado.aba) abrirAba(d.aba);
    if (d && d.linha && sheet) {
      // A linha N é a N-ésima linha não vazia da aba.
      const reais = estado.dados[d.aba].map((l, i) => [l, i]).filter(([l]) => !linhaVazia(d.aba, l));
      const alvo = reais[d.linha - 1];
      if (alvo) try { sheet.updateSelectionFromCoords(2, alvo[1], 2, alvo[1]); } catch (_e) { /* só destaque */ }
    }
  } finally {
    botao.disabled = false;
  }
}

function ligarEventos() {
  for (const b of $$('#ec-servicos [data-servico]')) b.addEventListener('click', () => alternarServico(b.dataset.servico, b));
  for (const t of $$('#ec-abas [data-aba]')) t.addEventListener('click', () => abrirAba(t.dataset.aba));
  $('#ec-cftv-save').addEventListener('click', salvar);
  $('#ec-cftv-add').addEventListener('click', () => sheet && sheet.insertRow());
  $('#ec-cftv-del').addEventListener('click', () => {
    if (!sheet) return;
    const linhas = sheet.getSelectedRows(true);
    if (!linhas || !linhas.length) { toast.info('Selecione a linha a remover.'); return; }
    sheet.deleteRow(Math.min(...linhas), linhas.length);
    marcarSujo(true);
  });
  $('#ec-cftv-reload').addEventListener('click', async () => {
    if (estado.sujo && !confirm('Descartar o que não foi salvo?')) return;
    aplicarEquipamentos(await pedir(`${BASE}/equipamentos`));
    abrirAba(estado.aba);
  });
  window.addEventListener('beforeunload', (ev) => {
    if (estado.sujo) { ev.preventDefault(); ev.returnValue = ''; }
  });
}

ligarEventos();
carregar()
  .then(() => abrirAba(location.hash.slice(1) || estado.servicos[0]))
  .catch((e) => toast.error(`Serviços do ambiente: ${e.message}`));
