// Menu lateral em árvore (expansão do NOC).
//
// - Ramos abrem e fecham pela seta; os abertos ficam guardados por usuário em
//   localStorage (`mm.menu.<usuário>`). O estado inicial é aplicado por um script
//   inline no partials/sidebar.html, antes do primeiro paint.
// - Telefonia, Equipamentos e as frentes seguem os módulos aceitos e o que há
//   cadastrado. O servidor já manda o menu certo; aqui ele é relido de
//   /api/menu ao carregar, a cada minuto e quando uma tela avisa que um módulo
//   mudou (`atualizarMenu()`), sem recarregar a página.
// - Telefonia desligada esconde SÓ o ramo do menu: as páginas continuam
//   abrindo pela URL e a coleta segue (regra do dono).
import { api } from '/static/js/api.js';

function nav() {
  return document.getElementById('mm-menu');
}

function chave(n) {
  return `mm.menu.${n.dataset.usuario || ''}`;
}

function guardarAbertos(n) {
  const abertos = [...n.querySelectorAll('.mm-ramo.aberto')].map((r) => r.dataset.ramo);
  try {
    localStorage.setItem(chave(n), JSON.stringify(abertos));
  } catch (_e) { /* localStorage bloqueado: vale só nesta página */ }
}

export function ligarMenu() {
  const n = nav();
  if (!n) return;
  n.addEventListener('click', (ev) => {
    const cab = ev.target.closest('[data-ramo-toggle]');
    if (!cab || !n.contains(cab)) return;
    const ramo = cab.parentElement;
    const aberto = ramo.classList.toggle('aberto');
    cab.setAttribute('aria-expanded', String(aberto));
    guardarAbertos(n);
  });
  // Os ramos que já nasceram abertos não deslizam no carregamento; daqui em
  // diante, abrir e fechar anima.
  requestAnimationFrame(() => requestAnimationFrame(() => n.classList.remove('mm-sem-animacao')));
}

// O que /api/menu devolve → o que aparece. Separado do fetch para ser lido sem rede.
export function aplicarMenu(m, n = nav()) {
  if (!n || !m) return;
  const visivel = { telefonia: !!m.telefonia, equipamentos: !!m.equipamentos };
  n.querySelectorAll('[data-visivel]').forEach((el) => {
    el.hidden = !visivel[el.dataset.visivel];
  });
  const frentes = m.frentes || {};
  n.querySelectorAll('[data-frente]').forEach((el) => {
    // O item da página atual nunca some debaixo do usuário.
    el.hidden = !frentes[el.dataset.frente] && el.getAttribute('aria-current') !== 'page';
  });
  const contagem = m.contagem || {};
  n.querySelectorAll('[data-contagem]').forEach((el) => {
    const v = contagem[el.dataset.contagem];
    el.textContent = v ? String(v) : '';
  });
}

export async function atualizarMenu() {
  if (!nav()) return;
  try {
    aplicarMenu(await api('/api/menu'));
  } catch (_e) { /* sem resposta: o menu fica como o servidor mandou */ }
}
