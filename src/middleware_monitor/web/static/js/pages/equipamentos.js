// Equipamentos — lista local, SÓ LEITURA. O cadastro nasce no NOC; aqui se vê o
// estado que o job de disponibilidade mantém. A frente vem da URL (?frente=).
import { api, qs } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';
import {
  ESTADOS, FRENTES, TIPOS, TIPOS_DE_VERIFICACAO, badge, badgeEstado, esc, quando, ESTADOS_DA_VERIFICACAO,
} from '/static/js/components/equipamentos.js';

const $ = (s) => document.getElementById(s);
const frente = new URLSearchParams(location.search).get('frente') || '';
let ultimo = null;

function origem(noc) {
  $('eq-origem-texto').innerHTML = noc.enrolado
    ? `Só leitura. O cadastro destes equipamentos nasce no NOC <strong class="font-mono">${esc(noc.url)}</strong>, ao qual este agente está enrolado — para incluir, mudar ou tirar um equipamento, use o NOC.`
    : 'Só leitura. Este agente ainda não está enrolado num NOC (Sistema → NOC); a lista de equipamentos chega por lá. O que estiver abaixo é o que o último NOC deixou.';
}

function abas(contagem) {
  const itens = [['', 'Todos', contagem.total], ...Object.entries(FRENTES).map(([k, v]) => [k, v, contagem[k]])];
  $('eq-frentes').innerHTML = itens.map(([k, rot, n]) => {
    const ativo = k === frente;
    return `<a href="/equipamentos${k ? `?frente=${k}` : ''}" ${ativo ? 'aria-current="page"' : ''}
      class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${ativo ? 'bg-blue-500/10 text-blue-300 ring-1 ring-blue-500/30' : 'text-gray-400 hover:text-gray-100 hover:bg-gray-700/60'}">
      ${esc(rot)}<span class="tabular-nums text-gray-500">${n ?? 0}</span></a>`;
  }).join('');
}

function chipsDeVerificacao(vs) {
  if (!vs.length) return '<span class="text-xs text-gray-500">nenhuma</span>';
  return `<div class="flex flex-wrap gap-1">${vs.map((v) => {
    const [tom, estado] = ESTADOS_DA_VERIFICACAO[v.estado] || ['gray', v.estado];
    return badge(tom, `${TIPOS_DE_VERIFICACAO[v.tipo] || v.tipo}`, { titulo: estado });
  }).join('')}</div>`;
}

function render(r) {
  ultimo = r;
  origem(r.noc);
  abas(r.contagem);
  $('eq-titulo').textContent = frente ? `Equipamentos · ${FRENTES[frente] || frente}` : 'Equipamentos';
  const busca = $('eq-busca').value.trim().toLowerCase();
  const itens = r.itens.filter((e) => !busca || e.nome.toLowerCase().includes(busca) || e.host.toLowerCase().includes(busca));
  $('eq-contagem').textContent = `${r.itens.length} ${r.itens.length === 1 ? 'equipamento' : 'equipamentos'}${busca ? ` · ${itens.length} na busca` : ''}`;

  const porEstado = {};
  for (const e of r.itens) porEstado[e.estado] = (porEstado[e.estado] || 0) + 1;
  $('eq-resumo').innerHTML = Object.entries(ESTADOS)
    .map(([k, [tom, rot]]) => badge(tom, `${porEstado[k] || 0} ${rot}`)).join('');

  if (!itens.length) {
    const vazio = r.itens.length
      ? 'Nada bate com a busca.'
      : frente ? `Nenhum equipamento de ${esc(FRENTES[frente] || frente)} cadastrado neste agente.` : 'Nenhum equipamento cadastrado neste agente. O cadastro é feito no NOC.';
    $('eq-tbody').innerHTML = `<tr><td colspan="7" class="px-4 py-8 text-center text-xs text-gray-500">${vazio}</td></tr>`;
    return;
  }
  $('eq-tbody').innerHTML = itens.map((e) => `
    <tr class="hover:bg-gray-800/60">
      <td class="px-4 py-2.5">
        <a href="/equipamentos/${encodeURIComponent(e.id)}" class="text-gray-100 hover:text-blue-300 font-medium">${esc(e.nome)}</a>
        ${e.fabricante || e.modelo ? `<div class="text-[11px] text-gray-500">${esc([e.fabricante, e.modelo].filter(Boolean).join(' · '))}</div>` : ''}
      </td>
      <td class="px-4 py-2.5 text-gray-300">${esc(TIPOS[e.tipo] || e.tipo)}</td>
      <td class="px-4 py-2.5 font-mono text-xs text-gray-300">${esc(e.host)}</td>
      <td class="px-4 py-2.5">${badgeEstado(e.estado)}</td>
      <td class="px-4 py-2.5 text-xs text-gray-400 whitespace-nowrap">${quando(e.estadoDesde)}</td>
      <td class="px-4 py-2.5">${chipsDeVerificacao(e.verificacoes)}</td>
      <td class="px-4 py-2.5 text-xs text-gray-400 whitespace-nowrap">${quando(e.ultimaLeitura, 'nunca')}</td>
    </tr>`).join('');
}

async function carregar() {
  try {
    render(await api(`/api/equipamentos${qs({ frente })}`));
  } catch (err) {
    toast.error(`Não deu para carregar: ${err.message}`);
  }
}

$('eq-busca').addEventListener('input', () => { if (ultimo) render(ultimo); });
injectIcons();
carregar();
// O estado muda sozinho (o job de disponibilidade roda no servidor).
setInterval(carregar, 30_000);
