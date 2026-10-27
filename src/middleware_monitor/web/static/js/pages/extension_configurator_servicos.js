// Serviços do ambiente (Telefone, DVR, Câmeras) e as abas DVR/Câmeras — NOC 1.4.0.
//
// A aba Telefones é a planilha de sempre (extension_configurator_detail.js). Este
// módulo só liga/desliga os serviços, troca as abas e cuida das planilhas de DVR e
// câmeras, que gravam na lista de equipamentos do agente (o mesmo caminho do NOC).
// Nada daqui fala com o DVR nem com a câmera.
import { toast } from '/static/js/components/toast.js';
import { ligarToque } from '/static/js/components/planilha_toque.js';
import { badge, esc } from '/static/js/components/equipamentos.js';

const envId = window.EC_ENV_ID;
const BASE = `/api/extension-configurator/environments/${encodeURIComponent(envId)}`;
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const ROTULO = { telefone: 'Telefones', dvr: 'DVR', camera: 'Câmeras' };
const ESTADO = { no_ar: '● no ar', fora: '● fora', degradado: '● degradado', pausada: '‖ pausada', desconhecido: '○ aguardando' };
// O rótulo da célula volta à chave para pintar a célula (a planilha só guarda o texto).
const ESTADO_DO_ROTULO = Object.fromEntries(Object.entries(ESTADO).map(([k, v]) => [v, k]));
// Na lista, o mesmo badge da tela de equipamentos.
const ESTADO_BADGE = {
  no_ar: ['green', 'no ar'], fora: ['red', 'fora'], degradado: ['yellow', 'com problema'],
  pausada: ['gray', 'pausada'], desconhecido: ['gray', 'aguardando'],
};
const SEM_CANAL = Number.MAX_SAFE_INTEGER;

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
    { type: 'text', name: 'nome', title: 'Nome', width: 260 },
    { type: 'text', name: 'ip', title: 'IP', width: 130 },
    { type: 'dropdown', name: 'dvr', title: 'DVR do ambiente', width: 230, source: [] },
    { type: 'numeric', name: 'canal', title: 'Canal', width: 70 },
    { type: 'text', name: 'fabricante', title: 'Fabricante', width: 130 },
    { type: 'text', name: 'modelo', title: 'Modelo', width: 130 },
    { type: 'text', name: '_estado', title: 'Estado', width: 130, readOnly: true },
  ],
};
const EDITAVEIS = {
  dvr: ['nome', 'ip', 'portaHttp', 'portaRtsp', 'adaptador', 'modelo', 'usuario', 'senha', 'canais'],
  camera: ['nome', 'ip', 'dvr', 'canal', 'fabricante', 'modelo'],
};
const AJUDA = {
  dvr: 'Uma linha por gravador. Porta RTSP em branco = sem verificar o vídeo. A senha nunca é mostrada: em branco mantém a guardada. O adaptador só lista os módulos de CFTV aceitos neste agente.',
  camera: 'Uma linha por câmera. Escolha o DVR do ambiente em que ela grava e o canal. Para ligar a um DVR novo, salve o DVR antes ou junto (o salvar grava as duas abas).',
  lista: 'As câmeras agrupadas pelo DVR em que gravam, na ordem dos canais. Para incluir, mudar ou tirar uma câmera, use a Planilha.',
};

const estado = {
  servicos: [],
  modelo: '',
  telefones: 0,
  adaptadores: [],
  dados: { dvr: [], camera: [] },  // linhas como objetos, inclusive as não salvas
  aba: null,
  sujo: false,
  vista: null,  // câmeras: 'lista' (ler, agrupada por DVR) ou 'planilha' (editar)
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
  // Só esta planilha. ``jspreadsheet.destroy(el, true)`` também tira os eventos do
  // documento e zera o jspreadsheet da página inteira (CE 4.x: ``jexcel = null``): a
  // planilha de telefones parava de responder e a próxima planilha de DVR/câmeras
  // não nascia mais ("Cannot set properties of null (setting 'current')").
  if (sheet) {
    try { sheet.destroy(); } catch (_e) { /* já destruída */ }
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
  // Algumas linhas livres no fim (o "+ Linha" acrescenta mais); vinte linhas vazias
  // deixavam a tabela parecendo maior do que o cadastro.
  while (data.length < Math.max(estado.dados[aba].length + 5, 8)) data.push(cols.map(() => ''));
  const editaveis = new Set(EDITAVEIS[aba].map((n) => cols.findIndex((c) => c.name === n)));
  const colEstado = cols.findIndex((c) => c.name === '_estado');
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
    updateTable: (_i, cell, col, _row, val) => {
      if (col !== colEstado) return;
      for (const k of Object.keys(ESTADO)) cell.classList.remove(`ec-cftv-estado-${k}`);
      const chave = ESTADO_DO_ROTULO[val];
      if (chave) cell.classList.add(`ec-cftv-estado-${chave}`);
    },
  });
  // iPad/celular: a mesma camada de toque da planilha de telefones (v2.19.2).
  ligarToque($('#ec-cftv-sheet'), sheet, { avisar: (msg, tipo) => (toast[tipo] || toast.info)(msg) });
}

// --- câmeras em lista, agrupadas pelo DVR ---------------------------------------------------

function porCanal(a, b) {
  const ca = Number.isInteger(a.canal) ? a.canal : SEM_CANAL;
  const cb = Number.isInteger(b.canal) ? b.canal : SEM_CANAL;
  return ca - cb || String(a.nome || '').localeCompare(String(b.nome || ''), 'pt-BR');
}

function badgeDoEstado(e) {
  const [tom, texto] = ESTADO_BADGE[e] || ['gray', e || '—'];
  return badge(tom, texto);
}

function chipDoCanal(cam, dvr, repetidos) {
  if (!Number.isInteger(cam.canal)) return '<span class="text-xs text-gray-500">—</span>';
  const acima = dvr && Number.isInteger(dvr.canais) && cam.canal > dvr.canais;
  const repetido = repetidos.has(cam.canal);
  const titulo = repetido ? 'Canal repetido neste DVR' : acima ? `O DVR tem ${dvr.canais} canais` : `Canal ${cam.canal}`;
  const tom = repetido || acima
    ? 'bg-yellow-500/10 text-yellow-300 ring-yellow-500/40'
    : 'bg-gray-900/70 text-gray-300 ring-gray-700';
  return `<span title="${esc(titulo)}" class="inline-flex items-center justify-center min-w-[2.25rem] px-1.5 py-0.5 rounded-md font-mono text-xs tabular-nums ring-1 ring-inset ${tom}">${String(cam.canal).padStart(2, '0')}</span>`;
}

function linhaDaCamera(cam, dvr, repetidos) {
  const marca = [cam.fabricante, cam.modelo].filter(Boolean).join(' · ');
  const nome = esc(cam.nome) || '<span class="text-gray-500">sem nome</span>';
  return `<tr class="hover:bg-gray-800/60">
    <td class="pl-3 sm:pl-4 pr-2 py-2.5 align-middle">${chipDoCanal(cam, dvr, repetidos)}</td>
    <td class="px-2 py-2.5 align-middle">
      <div class="text-gray-100 font-medium truncate" title="${esc(cam.nome)}">${nome}</div>
      <div class="sm:hidden font-mono text-[11px] text-gray-500 truncate">${esc(cam.ip)}</div>
    </td>
    <td class="px-2 py-2.5 align-middle">${badgeDoEstado(cam.estado)}</td>
    <td class="px-2 py-2.5 hidden sm:table-cell font-mono text-xs text-gray-300 whitespace-nowrap align-middle">${esc(cam.ip)}</td>
    <td class="px-2 py-2.5 hidden lg:table-cell text-xs text-gray-400 truncate align-middle" title="${esc(marca)}">${esc(marca) || '—'}</td>
    <td class="pl-2 pr-4 py-2.5 text-right whitespace-nowrap align-middle">
      <a href="/equipamentos/${encodeURIComponent(cam.id)}" class="text-xs font-medium text-blue-300 hover:text-blue-200" title="Verificações e eventos desta câmera"><span class="hidden sm:inline">Detalhes </span>→</a>
    </td>
  </tr>`;
}

function grupoDeCameras(dvr, cams) {
  const ordenadas = [...cams].sort(porCanal);
  const vistos = new Set();
  const repetidos = new Set();
  for (const c of ordenadas) {
    if (!Number.isInteger(c.canal)) continue;
    if (vistos.has(c.canal)) repetidos.add(c.canal);
    vistos.add(c.canal);
  }
  const titulo = dvr
    ? `<a href="/equipamentos/${encodeURIComponent(dvr.id)}" class="text-sm font-semibold text-gray-100 hover:text-blue-300 truncate min-w-0" title="${esc(dvr.nome)}">${esc(dvr.nome || dvr.ip)}</a>
       <span class="font-mono text-xs text-gray-400">${esc(dvr.ip)}</span>
       ${badgeDoEstado(dvr.estado)}`
    : '<span class="text-sm font-semibold text-gray-300">Sem DVR</span><span class="text-xs text-gray-500">câmeras sem gravador escolhido</span>';
  const canais = dvr && Number.isInteger(dvr.canais) ? ` · ${dvr.canais} canais` : '';
  const total = `${cams.length} ${cams.length === 1 ? 'câmera' : 'câmeras'}${canais}`;
  // table-fixed: nome comprido corta com reticências em vez de empurrar o estado para fora.
  const corpo = ordenadas.length
    ? `<table class="w-full text-sm table-fixed">
        <thead class="bg-gray-900/60 text-gray-400 text-[11px] uppercase tracking-wider">
          <tr>
            <th class="text-left pl-3 sm:pl-4 pr-2 py-2 font-semibold w-14 sm:w-[4.5rem]">Canal</th>
            <th class="text-left px-2 py-2 font-semibold">Câmera</th>
            <th class="text-left px-2 py-2 font-semibold w-24 sm:w-[8.5rem]">Estado</th>
            <th class="text-left px-2 py-2 font-semibold hidden sm:table-cell w-36">Endereço</th>
            <th class="text-left px-2 py-2 font-semibold hidden lg:table-cell w-56">Fabricante · modelo</th>
            <th class="pl-2 pr-4 py-2 w-10 sm:w-24"><span class="sr-only">Ações</span></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-700/60">${ordenadas.map((c) => linhaDaCamera(c, dvr, repetidos)).join('')}</tbody>
      </table>`
    : '<p class="px-4 py-4 text-xs text-gray-500">Nenhuma câmera neste DVR.</p>';
  return `<section class="bg-gray-800 ring-1 ring-gray-700 rounded-xl overflow-hidden">
    <header class="px-4 py-3 flex items-center gap-x-3 gap-y-1 flex-wrap border-b border-gray-700/60">
      ${titulo}
      <span class="ml-auto text-xs text-gray-500 tabular-nums">${total}</span>
    </header>
    ${corpo}
  </section>`;
}

function renderLista() {
  const alvo = $('#ec-cftv-lista');
  const dvrs = estado.dados.dvr.filter((l) => l.id);
  const cams = estado.dados.camera.filter((l) => l.id);
  if (!cams.length) {
    alvo.innerHTML = `<div class="bg-gray-800 ring-1 ring-gray-700 rounded-xl px-6 py-10 text-center">
      <p class="text-sm text-gray-200 font-medium">Nenhuma câmera neste ambiente.</p>
      <p class="text-xs text-gray-500 mt-1">Cadastre na planilha: uma linha por câmera, com o DVR e o canal em que ela grava.</p>
      <button type="button" data-ir-planilha class="ec-btn ec-btn-primary mt-4">Abrir a planilha</button>
    </div>`;
    return;
  }
  const refs = new Set(dvrs.map((d) => d.ref));
  const grupos = dvrs.map((d) => grupoDeCameras(d, cams.filter((c) => c.dvr === d.ref)));
  const soltas = cams.filter((c) => !refs.has(c.dvr));
  if (soltas.length) grupos.push(grupoDeCameras(null, soltas));
  alvo.innerHTML = grupos.join('');
}

function aplicarVista() {
  const lista = estado.aba === 'camera' && estado.vista === 'lista';
  $('#ec-cftv-vistas').classList.toggle('hidden', estado.aba !== 'camera');
  for (const b of $$('#ec-cftv-vistas [data-vista]')) {
    b.setAttribute('aria-pressed', String(b.dataset.vista === (lista ? 'lista' : 'planilha')));
  }
  $('#ec-cftv-lista').classList.toggle('hidden', !lista);
  $('#ec-cftv-sheet-wrap').classList.toggle('hidden', lista);
  // Na lista não há o que salvar nem linha para remover.
  $('#ec-cftv-save').classList.toggle('hidden', lista);
  $('#ec-cftv-del').classList.toggle('hidden', lista);
  $('#ec-cftv-ajuda').textContent = lista ? AJUDA.lista : AJUDA[estado.aba];
  if (lista) {
    destruir();
    renderLista();
  } else {
    montarPlanilha(estado.aba);
  }
}

function trocarVista(vista) {
  if (estado.aba !== 'camera' || vista === estado.vista) return;
  if (vista === 'lista') {
    lerPlanilha();
    if (estado.sujo) { toast.info('Salve ou recarregue a planilha antes de voltar à lista.'); return; }
  }
  estado.vista = vista;
  aplicarVista();
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
    const aviso = $('#ec-cftv-aviso');
    const semAdaptador = aba === 'dvr' && estado.adaptadores.length === 0;
    aviso.classList.toggle('hidden', !semAdaptador);
    aviso.innerHTML = semAdaptador
      ? 'Nenhum módulo de CFTV está aceito neste agente: o adaptador fica vazio. Ligue o do fabricante em <a href="/system/modulos" class="underline hover:text-amber-100">Sistema → Módulos</a>.'
      : '';
    // Câmeras abrem na lista quando já há câmera salva; sem nenhuma, direto na planilha.
    if (aba === 'camera' && (estado.vista === null || estado.sujo)) {
      estado.vista = !estado.sujo && estado.dados.camera.some((l) => l.id) ? 'lista' : 'planilha';
    }
    aplicarVista();
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
  const dvr = [...(eqs.dvr || [])].sort((a, b) => String(a.nome || '').localeCompare(String(b.nome || ''), 'pt-BR'));
  // Câmeras na ordem do DVR e do canal: é como se procura uma câmera no gravador.
  const ordemDoDvr = new Map(dvr.map((d, i) => [d.ref, i]));
  const camera = [...(eqs.camera || [])].sort((a, b) => (
    (ordemDoDvr.get(a.dvr) ?? dvr.length) - (ordemDoDvr.get(b.dvr) ?? dvr.length) || porCanal(a, b)
  ));
  estado.dados = { dvr, camera };
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
  $('#ec-cftv-add').addEventListener('click', () => {
    if (estado.aba === 'camera' && estado.vista === 'lista') trocarVista('planilha');
    if (sheet) sheet.insertRow();
  });
  for (const b of $$('#ec-cftv-vistas [data-vista]')) b.addEventListener('click', () => trocarVista(b.dataset.vista));
  $('#ec-cftv-lista').addEventListener('click', (ev) => {
    if (ev.target.closest('[data-ir-planilha]')) trocarVista('planilha');
  });
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
