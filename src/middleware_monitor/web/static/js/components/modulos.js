// Módulos aceitos (CONTRATO §13.8): uma chave por módulo. Usado por Sistema →
// Módulos (/system/modulos) e pela seção Módulos de Sistema → NOC.
//
// Quem liga e quando vem do servidor ("local:<usuário>" daqui, "noc:<email>"
// pelo NOC). Só admin troca; ao trocar, o menu lateral relê na hora.
import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';
import { atualizarMenu } from '/static/js/components/menu.js';
import { fmtTs } from '/static/js/util/datetime.js';

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const naoHomologado = '<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-yellow-500/15 text-yellow-400 ring-yellow-500/30"><span class="w-1.5 h-1.5 rounded-full bg-current"></span>não homologado</span>';

function quemEQuando(m) {
  if (!m.aceitoPor) return m.aceito ? 'ligado por padrão' : 'desligado por padrão';
  const quem = m.aceitoPor.startsWith('local:') ? `${m.aceitoPor.slice(6)} (aqui)`
    : m.aceitoPor.startsWith('noc:') ? `${m.aceitoPor.slice(4)} (pelo NOC)` : m.aceitoPor;
  return `${m.aceito ? 'ligado' : 'desligado'} por ${quem}${m.aceitoEm ? ` · ${fmtTs(m.aceitoEm)}` : ''}`;
}

export function montarModulos({ lista, soAdmin }) {
  function render(r) {
    const podeEditar = !!r.pode_editar;
    if (soAdmin) soAdmin.classList.toggle('hidden', podeEditar);
    lista.innerHTML = (r.modulos || []).map((m) => `
      <li class="px-5 py-3 flex items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-sm text-gray-100">${esc(m.rotulo)}</span>
            <span class="font-mono text-[11px] text-gray-500">${esc(m.nome)} · v${esc(m.versao)}</span>
            ${m.homologado ? '' : naoHomologado}
          </div>
          <div class="text-xs text-gray-500 mt-0.5">${esc(m.descricao)}</div>
          <div class="text-[11px] text-gray-500 mt-0.5">${esc(quemEQuando(m))}</div>
        </div>
        <label class="inline-flex items-center gap-2 text-xs text-gray-300 ${podeEditar ? 'cursor-pointer' : 'opacity-60'}">
          <input type="checkbox" data-modulo="${esc(m.nome)}" ${m.aceito ? 'checked' : ''} ${podeEditar ? '' : 'disabled'}
                 class="h-4 w-4 rounded border-gray-600 bg-gray-900 text-blue-500"/>
          ${m.aceito ? 'Ligado' : 'Desligado'}
        </label>
      </li>`).join('');
  }

  lista.addEventListener('change', async (ev) => {
    const caixa = ev.target.closest('input[data-modulo]');
    if (!caixa) return;
    const nome = caixa.dataset.modulo;
    const aceito = caixa.checked;
    // Telefonia é só sinal para o menu: desligar não para coleta, MQTT nem configurador.
    const efeito = nome === 'telefonia'
      ? 'Isso só esconde a telefonia do menu. As páginas continuam abrindo pelo endereço, e a coleta, o MQTT e o configurador continuam rodando.'
      : 'As verificações dele ficam guardadas e param de rodar até alguém ligar de novo.';
    if (!aceito && !confirm(`Desligar o módulo ${nome}?\n\n${efeito}`)) {
      caixa.checked = true;
      return;
    }
    caixa.disabled = true;
    try {
      render(await api(`/api/noc/modulos/${encodeURIComponent(nome)}`, { method: 'PUT', body: { aceito } }));
      toast.success(aceito ? `Módulo ${nome} ligado.` : `Módulo ${nome} desligado.`);
      atualizarMenu();
    } catch (err) {
      caixa.checked = !aceito;
      caixa.disabled = false;
      toast.error(err.message);
    }
  });

  async function carregar() {
    render(await api('/api/noc/modulos'));
  }

  return { carregar, render };
}
