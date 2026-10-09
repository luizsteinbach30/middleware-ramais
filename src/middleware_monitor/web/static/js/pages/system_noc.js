import { api } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { montarModulos } from '/static/js/components/modulos.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

const $ = (s) => document.getElementById(s);

const TONS = {
  green: 'bg-green-500/15 text-green-400 ring-green-500/30',
  red: 'bg-red-500/15 text-red-400 ring-red-500/30',
  yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30',
  blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30',
  gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30',
};

// A situação vem do servidor; aqui só se decide como ela se lê.
const SITUACOES = {
  nao_enrolado: ['gray', 'Não enrolado'],
  aguardando_primeiro_contato: ['blue', 'Enrolado — aguardando o primeiro contato'],
  conectado: ['green', 'Conectado'],
  sem_conexao: ['yellow', 'Sem conexão com o NOC'],
  credencial_recusada: ['red', 'Credencial recusada pelo NOC'],
  credencial_ilegivel: ['red', 'Credencial ilegível nesta máquina'],
  revogado: ['red', 'Revogado no NOC'],
};

function badge(tom, texto) {
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${TONS[tom]}"><span class="w-1.5 h-1.5 rounded-full bg-current"></span>${texto}</span>`;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function descreveRelogio(offset) {
  if (offset === null || offset === undefined) return '—';
  const abs = Math.abs(offset);
  if (abs <= 5) return 'Certo (diferença de até 5 s para o NOC)';
  const quanto = abs >= 120 ? `${Math.round(abs / 60)} min` : `${abs} s`;
  // Positivo: o NOC está à frente, então o relógio daqui está atrasado.
  return offset > 0 ? `${quanto} atrasado em relação ao NOC` : `${quanto} adiantado em relação ao NOC`;
}

let estadoAtual = null;

const modulos = montarModulos({ lista: $('noc-modulos-lista'), soAdmin: $('noc-modulos-so-admin') });

// Rótulo de cada ação do executor. A LISTA vem do manifesto; ação sem rótulo
// aqui aparece pelo nome técnico — nunca some.
const ROTULOS_DAS_ACOES = {
  ping: 'Pingar o aparelho de um ramal',
  status_do_ramal: 'Estado de um ramal no USCall',
  coletar_agora: 'Coletar do USCall agora',
  inventario: 'Inventário deste agente',
  capacidades: 'Mandar o manifesto de novo',
  logs: 'Ler os logs',
  normalize: 'Normalizar o aparelho de um ramal',
  reaplicar_config_do_ambiente: 'Reaplicar a configuração de um ambiente',
  editar_planilha_do_ambiente: 'Editar a planilha de um ambiente',
  editar_config_do_ambiente: 'Editar a config padrão de um ambiente',
  abrir_acesso_web: 'Abrir acesso pelo túnel (web, RDP, SSH)',
  verificar_equipamento: 'Verificar um equipamento agora',
  definir_equipamentos: 'Definir a lista de equipamentos',
  definir_modulos: 'Ligar e desligar módulos',
};

function render(e) {
  estadoAtual = e;
  const [tom, texto] = SITUACOES[e.situacao] || ['gray', e.situacao];
  $('noc-situacao').innerHTML = badge(tom, texto);
  $('noc-agente').textContent = e.agente_id || '';

  const detalhe = $('noc-detalhe');
  detalhe.textContent = e.detalhe || '';
  detalhe.classList.toggle('hidden', !e.detalhe);

  $('noc-url').textContent = e.url;
  $('noc-contato').textContent = e.ultimo_contato_em ? fmtTs(e.ultimo_contato_em) : (e.enrolado ? 'Nunca' : '—');
  $('noc-intervalo').textContent = e.enrolado ? `a cada ${e.intervalo_heartbeat_s} s` : '—';
  const desatualizado = e.versao_desejada && e.versao_desejada !== e.versao_atual;
  $('noc-versao').innerHTML = `<span class="font-mono">${esc(e.versao_atual)}</span>` +
    (desatualizado ? ` <span class="text-xs text-yellow-400">· o NOC espera ${esc(e.versao_desejada)}</span>` : '');
  $('noc-relogio').textContent = descreveRelogio(e.relogio_offset_s);
  $('noc-enrolado-em').textContent = e.enrolado_em ? fmtTs(e.enrolado_em) : '—';
  // Falha de entrega não apaga o último envio bom: as duas coisas aparecem.
  const tel = $('noc-telemetria');
  tel.textContent = !e.enrolado
    ? '—'
    : e.telemetria_detalhe
      ? `pendente — ${e.telemetria_detalhe}`
      : e.telemetria_enviada_em ? `entregue ${fmtTs(e.telemetria_enviada_em)}` : 'ainda não enviada';
  tel.className = `mt-1 ${e.telemetria_detalhe ? 'text-yellow-400 text-xs' : 'text-gray-200'}`;
  $('noc-certificado').textContent = e.certificado_expira_em ? fmtTs(e.certificado_expira_em) : '—';
  // O que o NOC executou aqui: a última tarefa e o que ainda não foi confirmado lá.
  const t = e.tarefas || {};
  const ult = t.ultima;
  const tar = $('noc-tarefas');
  tar.textContent = !ult
    ? (e.enrolado ? 'nenhuma recebida' : '—')
    : `${ult.tipo} ${ult.ok ? 'ok' : 'falhou'} · ${fmtTs(ult.concluida_em)}` +
      (ult.pedida_por ? ` · ${ult.pedida_por}` : '') +
      (t.a_entregar ? ` · ${t.a_entregar} resultado(s) a entregar` : '');
  tar.className = `mt-1 ${ult && !ult.ok ? 'text-yellow-400 text-xs' : 'text-gray-200'}`;

  $('noc-testar').classList.toggle('hidden', !e.enrolado);
  $('noc-desenrolar').classList.toggle('hidden', !e.enrolado);

  const url = $('noc-url-input');
  if (document.activeElement !== url) url.value = e.url;
  $('noc-url-aviso').classList.toggle('hidden', !url.value.trim().toLowerCase().startsWith('http://'));
  $('noc-enrolar-titulo').textContent = e.enrolado ? 'Enrolar de novo com um código novo' : 'Enrolar este agente';
  $('noc-reenrolar-aviso').classList.toggle('hidden', !e.enrolado);
  injectIcons();
}

function renderAcoes(m) {
  const acoes = m.acoes || [];
  const raio = m.raio_das_acoes || {};
  const escrita = acoes.filter((a) => raio[a] && raio[a] !== 'LEITURA').length;
  $('noc-acoes-resumo').textContent = acoes.length
    ? `${acoes.length} ${acoes.length === 1 ? 'ação' : 'ações'} · ${acoes.length - escrita} de leitura, ${escrita} de escrita`
    : '';
  $('noc-acoes').innerHTML = acoes.length
    ? acoes.map((a) => {
      const tipo = raio[a] === 'LEITURA' ? badge('blue', 'leitura') : badge('yellow', 'escrita');
      return `<li class="flex items-center justify-between gap-3"><span class="min-w-0"><span class="text-gray-200">${esc(ROTULOS_DAS_ACOES[a] || a)}</span> <span class="font-mono text-[11px] text-gray-500">${esc(a)}</span></span>${tipo}</li>`;
    }).join('')
    : '<li class="text-xs text-gray-500">Este agente não declara nenhuma ação: o NOC não pode pedir nada a ele.</li>';
}

function renderManifesto(m) {
  renderAcoes(m);
  const modelos = m.modelos || [];
  $('noc-modelos').innerHTML = modelos.length
    ? modelos.map((x) => `<li class="flex items-center justify-between gap-3"><span class="font-mono text-xs text-gray-200">${esc(x.modelo)}</span><span class="text-xs text-gray-400 tabular-nums">${x.quantidade} ${x.quantidade === 1 ? 'linha' : 'linhas'}</span></li>`).join('')
    : '<li class="text-xs text-gray-500">Nenhum ambiente cadastrado.</li>';

  const servidores = m.uscall || [];
  $('noc-uscall').innerHTML = servidores.length
    ? servidores.map((s) => {
      const estado = s.alcancavel === true ? badge('green', 'respondeu') : s.alcancavel === false ? badge('red', 'não respondeu') : badge('gray', 'sem coleta ainda');
      return `<li class="flex items-center justify-between gap-3"><span class="text-gray-200">${esc(s.nome)} <span class="font-mono text-[11px] text-gray-500">${esc(s.endereco)}</span></span>${estado}</li>`;
    }).join('')
    : '<li class="text-xs text-gray-500">Nenhum servidor USCall habilitado.</li>';
}

async function carregar() {
  try {
    const [e, m] = await Promise.all([api('/api/noc'), api('/api/noc/manifesto'), modulos.carregar()]);
    render(e);
    renderManifesto(m);
  } catch (err) {
    toast.error(`Não deu para carregar: ${err.message}`);
  }
}

$('noc-url-input').addEventListener('input', (ev) => {
  $('noc-url-aviso').classList.toggle('hidden', !ev.target.value.trim().toLowerCase().startsWith('http://'));
});

$('noc-codigo').addEventListener('input', (ev) => {
  ev.target.value = ev.target.value.toUpperCase();
});

$('noc-enrolar').addEventListener('click', async () => {
  const codigo = $('noc-codigo').value.trim();
  if (codigo.replace(/[^0-9A-Z]/gi, '').length < 12) {
    toast.error('O código tem 12 caracteres, no formato XXXX-XXXX-XXXX.');
    return;
  }
  const btn = $('noc-enrolar');
  btn.disabled = true;
  btn.textContent = 'Enrolando…';
  try {
    const e = await api('/api/noc/enrolar', { method: 'POST', body: { codigo, url: $('noc-url-input').value.trim() } });
    $('noc-codigo').value = '';
    render(e);
    if (e.situacao === 'conectado') toast.success('Enrolado e conectado ao NOC.');
    else toast.info('Enrolado. O primeiro contato ainda não respondeu — veja o detalhe.');
  } catch (err) {
    toast.error(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Enrolar';
  }
});

$('noc-testar').addEventListener('click', async () => {
  const btn = $('noc-testar');
  btn.disabled = true;
  try {
    const e = await api('/api/noc/heartbeat', { method: 'POST' });
    render(e);
    if (e.situacao === 'conectado') toast.success('O NOC respondeu.');
    else toast.error(e.detalhe || 'O NOC não respondeu.');
  } catch (err) {
    toast.error(err.message);
  } finally {
    btn.disabled = false;
  }
});

$('noc-desenrolar').addEventListener('click', async () => {
  const id = estadoAtual?.agente_id || '';
  // O NOC continua conhecendo este agente: desenrolar aqui não revoga lá.
  if (!confirm(`Apagar a credencial deste agente (${id})?\n\nIsso NÃO revoga o agente no NOC — revogue lá também.`)) return;
  try {
    render(await api('/api/noc/desenrolar', { method: 'POST' }));
    toast.success('Credencial apagada desta máquina.');
  } catch (err) {
    toast.error(err.message);
  }
});

carregar();
// A situação muda sozinha (o heartbeat roda no servidor): relê a cada 30 s.
setInterval(() => { api('/api/noc').then(render).catch(() => {}); }, 30_000);
