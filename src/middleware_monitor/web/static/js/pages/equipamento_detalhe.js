// Equipamento — detalhe local, SÓ LEITURA. O cadastro nasce no NOC.
// "Verificar agora" (admin) roda as verificações com a mesma função do verbo
// verificar_equipamento do NOC: leitura avulsa, não muda estado nem histórico.
import { api } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';
import {
  FRENTES, TIPOS, TIPOS_DE_VERIFICACAO, badge, badgeEstado, badgeVerificacao, esc, leituraDoGravador, parametros, quando,
} from '/static/js/components/equipamentos.js';

const $ = (s) => document.getElementById(s);
const id = $('eq-pagina').dataset.equipamento;

const EVENTOS = {
  'equipamento.caiu': ['red', 'caiu'],
  'equipamento.voltou': ['green', 'voltou'],
  'equipamento.degradou': ['yellow', 'com problema'],
  'equipamento.normalizou': ['green', 'normalizou'],
  'cftv.canal_sem_video': ['red', 'canal sem vídeo'],
  'cftv.canal_voltou': ['green', 'canal voltou'],
  'cftv.parou_de_gravar': ['red', 'parou de gravar'],
  'cftv.voltou_a_gravar': ['green', 'voltou a gravar'],
  'cftv.disco_com_defeito': ['red', 'disco com problema'],
  'cftv.disco_normalizou': ['green', 'disco normalizou'],
  'cftv.hora_errada': ['yellow', 'hora errada'],
  'cftv.hora_certa': ['green', 'hora certa'],
};

function intervalo(s) {
  if (!s) return '—';
  if (s % 3600 === 0) return `a cada ${s / 3600} h`;
  if (s % 60 === 0) return `a cada ${s / 60} min`;
  return `a cada ${s} s`;
}

function campo(rotulo, valor, { mono = false } = {}) {
  return `<div><dt class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">${esc(rotulo)}</dt>
    <dd class="mt-1 text-gray-200 break-all ${mono ? 'font-mono text-xs' : ''}">${valor}</dd></div>`;
}

function origem(noc) {
  $('eq-origem-texto').innerHTML = noc.enrolado
    ? `Só leitura. O cadastro deste equipamento nasce no NOC <strong class="font-mono">${esc(noc.url)}</strong>, ao qual este agente está enrolado — para mudar alguma coisa, use o NOC.`
    : 'Só leitura. Este agente ainda não está enrolado num NOC (Sistema → NOC); o cadastro de equipamentos chega por lá.';
}

function render(e) {
  $('eq-nao-achou').classList.add('hidden');
  $('eq-conteudo').classList.remove('hidden');
  document.title = `${e.nome} — Middleware Monitor`;
  origem(e.noc);
  $('eq-nome').textContent = e.nome;
  $('eq-sub').textContent = `${TIPOS[e.tipo] || e.tipo} · ${FRENTES[e.frente] || e.frente} · ${e.host}`;
  $('eq-voltar').href = `/equipamentos?frente=${encodeURIComponent(e.frente)}`;
  $('eq-verificar').classList.toggle('hidden', !e.pode_verificar);

  $('eq-estado').innerHTML = badgeEstado(e.estado);
  $('eq-desde').textContent = e.estadoDesde ? `desde ${quando(e.estadoDesde)}` : '';
  $('eq-cadastro').innerHTML = [
    campo('Tipo', esc(TIPOS[e.tipo] || e.tipo)),
    campo('Endereço', esc(e.host), { mono: true }),
    campo('MAC', esc(e.mac || '—'), { mono: true }),
    campo('Fabricante / modelo', esc([e.fabricante, e.modelo].filter(Boolean).join(' · ') || '—')),
    campo('Ligado a', e.pai ? `<a class="text-blue-300 hover:underline" href="/equipamentos/${encodeURIComponent(e.pai.id)}">${esc(e.pai.nome)}</a>` : '—'),
    campo('Cai depois de', `${e.tentativasParaCair} ${e.tentativasParaCair === 1 ? 'falha' : 'falhas seguidas'}`),
    campo('Id neste agente', esc(e.id), { mono: true }),
    campo('Id no NOC', esc(e.ref), { mono: true }),
  ].join('');

  $('eq-verificacoes').innerHTML = e.verificacoes.length ? e.verificacoes.map((v) => {
    const estado = v.pausada
      ? `${badgeVerificacao('pausada')}<div class="text-[11px] text-gray-500 mt-1">módulo ${esc(v.moduloRotulo)} desligado</div>`
      : `${badgeVerificacao(v.estado)}${v.estadoDesde ? `<div class="text-[11px] text-gray-500 mt-1">desde ${quando(v.estadoDesde)}</div>` : ''}`;
    return `<tr class="align-top">
      <td class="px-4 py-2.5 text-gray-200 whitespace-nowrap">${esc(TIPOS_DE_VERIFICACAO[v.tipo] || v.tipo)}<div class="font-mono text-[11px] text-gray-500">${esc(v.tipo)}</div></td>
      <td class="px-4 py-2.5 text-xs text-gray-400">${esc(parametros(v.tipo, v.parametros))}</td>
      <td class="px-4 py-2.5">${estado}</td>
      <td class="px-4 py-2.5 text-right text-xs text-gray-300 tabular-nums whitespace-nowrap">${v.ultimoValor === null || v.ultimoValor === undefined ? '—' : `${v.ultimoValor} ms`}</td>
      <td class="px-4 py-2.5 text-xs ${v.ultimoErro ? 'text-red-300' : 'text-gray-500'}">${esc(v.ultimoErro || '—')}${v.falhasSeguidas && !v.pausada ? `<div class="text-[11px] text-gray-500 mt-0.5">${v.falhasSeguidas} falha(s) seguida(s)</div>` : ''}</td>
      <td class="px-4 py-2.5 text-xs text-gray-400 whitespace-nowrap">${intervalo(v.intervaloS)}</td>
      <td class="px-4 py-2.5 text-xs text-gray-400 whitespace-nowrap">${quando(v.ultimaEm, 'nunca')}</td>
    </tr>`;
  }).join('') : '<tr><td colspan="7" class="px-4 py-6 text-center text-xs text-gray-500">Nenhuma verificação cadastrada.</td></tr>';

  const temCftv = e.verificacoes.some((v) => v.tipo === 'cftv');
  $('eq-cftv-card').classList.toggle('hidden', !temCftv && !e.cftv);
  if (e.cftv) {
    $('eq-cftv-lido').textContent = `lido ${quando(e.cftv.lidoEm)}`;
    $('eq-cftv').innerHTML = leituraDoGravador(e.cftv);
  } else {
    const pausada = e.verificacoes.find((v) => v.tipo === 'cftv' && v.pausada);
    $('eq-cftv-lido').textContent = '';
    $('eq-cftv').innerHTML = `<div class="text-xs text-gray-500">${pausada
      ? `Ainda sem leitura: o módulo ${esc(pausada.moduloRotulo)} está desligado neste agente (Sistema → Módulos).`
      : 'Ainda sem leitura do gravador.'}</div>`;
  }

  $('eq-credenciais').innerHTML = e.credenciais.length ? e.credenciais.map((c) => `
    <li class="px-5 py-2.5 flex items-center justify-between gap-3 text-sm">
      <span class="min-w-0"><span class="font-mono text-xs text-gray-300">${esc(c.tipo)}</span>
        <span class="text-gray-200 ml-2">${esc(c.usuario || '(sem usuário)')}</span></span>
      ${c.definida ? badge('green', 'senha definida') : badge('gray', 'sem senha')}
    </li>`).join('') : '<li class="px-5 py-4 text-xs text-gray-500">Nenhuma credencial guardada para este equipamento.</li>';

  $('eq-eventos').innerHTML = e.eventos.length ? e.eventos.map((ev) => {
    const [tom, rot] = EVENTOS[ev.tipo] || ['gray', ev.tipo];
    return `<li class="px-5 py-2.5 text-sm">
      <div class="flex items-center justify-between gap-3">${badge(tom, rot)}<span class="text-xs text-gray-500 whitespace-nowrap">${quando(ev.em)}</span></div>
      <div class="text-xs text-gray-400 mt-1">${esc(ev.de)} → ${esc(ev.para)}${ev.detalhe ? ` · ${esc(ev.detalhe)}` : ''}</div>
    </li>`;
  }).join('') : '<li class="px-5 py-4 text-xs text-gray-500">Nenhum evento ainda.</li>';
  injectIcons();
}

function renderAgora(r) {
  const vs = r.verificacoes || [];
  const linhas = vs.map((v) => {
    const pausada = typeof v.erro === 'string' && v.erro.startsWith('pausada');
    const estado = pausada ? badgeVerificacao('pausada') : v.ok ? badgeVerificacao('ok') : badgeVerificacao('falha');
    return `<li class="py-2 flex items-start justify-between gap-4 text-sm">
      <span class="min-w-0"><span class="text-gray-200">${esc(TIPOS_DE_VERIFICACAO[v.tipo] || v.tipo)}</span>
        ${v.erro ? `<div class="text-xs ${pausada ? 'text-gray-500' : 'text-red-300'} mt-0.5">${esc(v.erro)}</div>` : ''}</span>
      <span class="flex items-center gap-3 shrink-0">${v.valor === null || v.valor === undefined ? '' : `<span class="text-xs text-gray-400 tabular-nums">${v.valor} ms</span>`}${estado}</span>
    </li>`;
  }).join('');
  const leitura = vs.find((v) => v.cftv);
  $('eq-agora-corpo').innerHTML = `
    <ul class="divide-y divide-gray-700/60">${linhas || '<li class="text-xs text-gray-500">Nenhuma verificação para rodar.</li>'}</ul>
    ${leitura ? `<div class="pt-4 border-t border-gray-700/60 space-y-5"><div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Leitura do gravador agora</div>${leituraDoGravador(leitura.cftv)}</div>` : ''}`;
  $('eq-agora').classList.remove('hidden');
  injectIcons();
}

async function carregar() {
  try {
    render(await api(`/api/equipamentos/${encodeURIComponent(id)}`));
  } catch (err) {
    if (err.status === 404) {
      $('eq-conteudo').classList.add('hidden');
      $('eq-nao-achou').classList.remove('hidden');
      $('eq-nome').textContent = 'Equipamento não encontrado';
      $('eq-sub').textContent = id;
      $('eq-verificar').classList.add('hidden');
      return;
    }
    toast.error(`Não deu para carregar: ${err.message}`);
  }
}

$('eq-verificar').addEventListener('click', async () => {
  const btn = $('eq-verificar');
  const rotulo = btn.querySelector('span:last-child');
  btn.disabled = true;
  rotulo.textContent = 'Verificando…';
  try {
    renderAgora(await api(`/api/equipamentos/${encodeURIComponent(id)}/verificar`, { method: 'POST' }));
  } catch (err) {
    toast.error(err.message);
  } finally {
    btn.disabled = false;
    rotulo.textContent = 'Verificar agora';
  }
});

$('eq-agora-fechar').addEventListener('click', () => $('eq-agora').classList.add('hidden'));

injectIcons();
carregar();
setInterval(carregar, 30_000);
