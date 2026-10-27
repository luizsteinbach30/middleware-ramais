// Planilha no toque (iPad, celular) — v2.19.2.
//
// O Jspreadsheet 4 foi feito para mouse e teclado. No iPad (pedido do dono, 27/10: "não consigo digitar nem
// copiar e arrastar as informações da planilha"):
//   - o editor só abria segurando o dedo meio segundo — o toque normal só selecionava;
//   - arrastar o dedo rola a tela: não há como selecionar várias células;
//   - copiar, colar, apagar e a alça de preencher (o quadradinho do canto) só existem com mouse/teclado.
//
// Esta camada troca os toques do Jspreadsheet pelos nossos e põe uma barra acima da planilha:
//   - toque seleciona a célula; **tocar de novo na mesma célula abre a edição** com o teclado (o foco acontece
//     dentro do toque — é a única forma de o iOS abrir o teclado);
//   - **Selecionar**: enquanto ligado, arrastar o dedo marca células em vez de rolar;
//   - **Copiar / Colar**: pela área de transferência do sistema quando o navegador deixa (HTTPS — o acesso pelo
//     NOC), e sempre por uma área interna da página (o IP:8080 em http não tem a API de área de transferência);
//   - **Preencher ↓**: o mesmo que puxar a alça — a primeira linha da seleção desce pelas outras, com a sequência
//     numérica que a página souber calcular (`valorDoPreenchimento`);
//   - **Apagar**: limpa as células selecionadas (o Delete do teclado).
// Mouse e teclado continuam iguais: nada aqui roda sem tela de toque.

const ESTILO_ID = 'pl-toque-estilo';

export function temToque() {
  return typeof navigator !== 'undefined' && (navigator.maxTouchPoints > 0 || 'ontouchstart' in window);
}

function injetarEstilo() {
  if (document.getElementById(ESTILO_ID)) return;
  const s = document.createElement('style');
  s.id = ESTILO_ID;
  s.textContent = `
    .pl-toque { touch-action: manipulation; -webkit-user-select: none; user-select: none; }
    .pl-toque input, .pl-toque textarea { -webkit-user-select: text; user-select: text; }
    .pl-toque.pl-selecionando .jexcel_content, .pl-toque.pl-selecionando table { touch-action: none; }
    .pl-toque-barra { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; margin: 0 0 .5rem; }
    .pl-toque-barra button {
      min-height: 44px; padding: 0 1rem; border-radius: .5rem; font-size: .875rem; font-weight: 600;
      background: #1f2937; color: #e5e7eb; border: 1px solid #374151;
    }
    .pl-toque-barra button[aria-pressed="true"] { background: #2563eb; border-color: #3b82f6; color: #fff; }
    .pl-toque-barra button:disabled { opacity: .45; }
    .pl-toque-barra .pl-dica { font-size: .75rem; color: #9ca3af; }
  `;
  document.head.appendChild(s);
}

/** A seleção atual como [x1, y1, x2, y2] em ordem crescente, ou null. */
function faixaDe(sheet) {
  const sc = sheet.selectedCell;
  if (!Array.isArray(sc) || sc.length < 4) return null;
  const [a, b, c, d] = sc.map(Number);
  if ([a, b, c, d].some(Number.isNaN)) return null;
  return [Math.min(a, c), Math.min(b, d), Math.max(a, c), Math.max(b, d)];
}

function copiarPeloSistema(texto) {
  // A API nova só existe em HTTPS; o caminho antigo cobre o http do IP:8080. Falhar aqui não é erro: a área
  // interna da página guarda a cópia de qualquer jeito.
  try {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(texto).catch(() => {});
      return;
    }
  } catch (_e) { /* segue para o caminho antigo */ }
  try {
    const t = document.createElement('textarea');
    t.value = texto;
    t.setAttribute('readonly', '');
    t.style.position = 'fixed';
    t.style.opacity = '0';
    document.body.appendChild(t);
    t.focus();
    t.setSelectionRange(0, texto.length);
    document.execCommand('copy');
    t.remove();
  } catch (_e) { /* sem área do sistema: fica só a interna */ }
}

/**
 * Liga a camada de toque numa planilha recém-criada. Chame de novo a cada `jspreadsheet(...)`: o Jspreadsheet
 * religa os toques dele no `document` a cada criação. Devolve uma função que desliga.
 *
 * @param {HTMLElement} container  o elemento passado ao `jspreadsheet(...)`
 * @param {object} sheet           a instância
 * @param {object} [opcoes]
 * @param {(x:number, yFonte:number, deslocamento:number) => any} [opcoes.valorDoPreenchimento]
 *        valor da célula `deslocamento` linhas abaixo da fonte; o padrão repete o valor
 * @param {(msg:string, tipo?:string) => void} [opcoes.avisar]
 */
export function ligarToque(container, sheet, opcoes = {}) {
  if (!temToque() || !container || !sheet) return () => {};
  const J = window.jspreadsheet || window.jexcel;
  if (J) {
    document.removeEventListener('touchstart', J.touchStartControls);
    document.removeEventListener('touchend', J.touchEndControls);
    document.removeEventListener('touchcancel', J.touchEndControls);
    document.removeEventListener('touchmove', J.touchEndControls);
  }
  injetarEstilo();
  const avisar = opcoes.avisar || (() => {});
  const valorDoPreenchimento = opcoes.valorDoPreenchimento || ((x, yFonte) => sheet.getValueFromCoords(x, yFonte));
  container.classList.add('pl-toque');

  let selecionando = false;
  let inicio = null;   // { x, y, td, ancora: [x, y], moveu }
  let areaInterna = '';

  const celula = (el) => {
    const td = el && el.closest ? el.closest('td[data-x][data-y]') : null;
    return td && container.contains(td) ? td : null;
  };
  const tornarAtual = () => { if (J) J.current = sheet; };
  const editavel = (td) => {
    if (td.classList.contains('readonly')) return false;
    const col = (sheet.options.columns || [])[Number(td.dataset.x)] || {};
    return !col.readOnly && !['checkbox', 'html', 'image', 'hidden'].includes(col.type);
  };

  const aoTocar = (e) => {
    if (e.touches.length > 1) { inicio = null; return; }
    if (sheet.edition && e.target.closest && e.target.closest('input, textarea, select')) { inicio = null; return; }
    const td = celula(e.target);
    if (!td) { inicio = null; return; }
    const t = e.touches[0];
    const x = Number(td.dataset.x);
    const y = Number(td.dataset.y);
    inicio = { x: t.clientX, y: t.clientY, td, ancora: [x, y], moveu: false };
    if (selecionando) {
      e.preventDefault();
      tornarAtual();
      if (sheet.edition) sheet.closeEditor(sheet.edition[0], true);
      sheet.updateSelectionFromCoords(x, y, x, y);
    }
  };

  const aoMover = (e) => {
    if (!inicio) return;
    const t = e.touches[0];
    if (selecionando) {
      e.preventDefault();
      const td = celula(document.elementFromPoint(t.clientX, t.clientY));
      if (td) sheet.updateSelectionFromCoords(inicio.ancora[0], inicio.ancora[1], Number(td.dataset.x), Number(td.dataset.y));
      return;
    }
    if (Math.abs(t.clientX - inicio.x) > 10 || Math.abs(t.clientY - inicio.y) > 10) inicio.moveu = true;
  };

  const aoSoltar = () => {
    const toque = inicio;
    inicio = null;
    if (!toque || selecionando || toque.moveu) return;   // rolagem ou seleção por arrasto: nada a fazer
    const { td } = toque;
    const x = Number(td.dataset.x);
    const y = Number(td.dataset.y);
    tornarAtual();
    const f = faixaDe(sheet);
    const jaSelecionada = f && f[0] === x && f[2] === x && f[1] === y && f[3] === y;
    if (sheet.edition) sheet.closeEditor(sheet.edition[0], true);
    if (jaSelecionada && editavel(td)) {
      // Dentro do toque: o openEditor dá foco ao campo e o iOS abre o teclado.
      sheet.openEditor(td, false);
    } else {
      sheet.updateSelectionFromCoords(x, y, x, y);
    }
  };

  container.addEventListener('touchstart', aoTocar, { passive: false });
  container.addEventListener('touchmove', aoMover, { passive: false });
  container.addEventListener('touchend', aoSoltar);
  container.addEventListener('touchcancel', () => { inicio = null; });

  // ----- a barra -------------------------------------------------------------
  const ancoraDaBarra = container.closest('.ec-spreadsheet-wrap') || container;
  const anterior = ancoraDaBarra.previousElementSibling;
  if (anterior && anterior.classList.contains('pl-toque-barra')) anterior.remove();
  const barra = document.createElement('div');
  barra.className = 'pl-toque-barra';
  // O toque num botão vira um mousedown emulado, e o Jspreadsheet (que escuta no document) desfaz a seleção de
  // quem clica fora da planilha — o botão chegava sem nada selecionado. A barra guarda o mousedown para si.
  barra.addEventListener('mousedown', (e) => e.stopPropagation());
  const botao = (texto, titulo, acao) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = texto;
    b.title = titulo;
    b.addEventListener('click', (e) => { e.preventDefault(); acao(b); });
    barra.appendChild(b);
    return b;
  };

  const exigirFaixa = () => {
    const f = faixaDe(sheet);
    if (!f) avisar('Toque numa célula primeiro.', 'info');
    return f;
  };

  const btnSelecionar = botao('Selecionar', 'Ligado: arrastar o dedo marca várias células em vez de rolar', (b) => {
    selecionando = !selecionando;
    b.setAttribute('aria-pressed', String(selecionando));
    container.classList.toggle('pl-selecionando', selecionando);
  });
  btnSelecionar.setAttribute('aria-pressed', 'false');

  botao('Copiar', 'Copiar as células selecionadas', () => {
    const f = exigirFaixa();
    if (!f) return;
    const [x1, y1, x2, y2] = f;
    const linhas = [];
    for (let y = y1; y <= y2; y++) {
      const cols = [];
      for (let x = x1; x <= x2; x++) cols.push(String(sheet.getValueFromCoords(x, y) ?? '').replace(/[\t\n\r]+/g, ' '));
      linhas.push(cols.join('\t'));
    }
    areaInterna = linhas.join('\n');
    copiarPeloSistema(areaInterna);
    const n = (x2 - x1 + 1) * (y2 - y1 + 1);
    avisar(n === 1 ? 'Célula copiada.' : `${n} células copiadas.`, 'success');
  });

  botao('Colar', 'Colar a partir da célula selecionada', async () => {
    const f = exigirFaixa();
    if (!f) return;
    let texto = '';
    try {
      if (navigator.clipboard && navigator.clipboard.readText && window.isSecureContext) texto = await navigator.clipboard.readText();
    } catch (_e) { /* o navegador recusou: usa a cópia feita aqui */ }
    if (!texto) texto = areaInterna;
    if (!texto) { avisar('Nada para colar: copie células antes (ou copie um texto em outro app, com o acesso pelo NOC).', 'info'); return; }
    tornarAtual();
    sheet.paste(f[0], f[1], texto.replace(/\r\n?/g, '\n').replace(/\n$/, ''));
  });

  botao('Preencher ↓', 'Descer a primeira linha da seleção pelas outras (como puxar a alça), com sequência numérica', () => {
    const f = exigirFaixa();
    if (!f) return;
    const [x1, y1, x2, y2] = f;
    if (y2 <= y1) { avisar('Selecione a linha de origem e as linhas de baixo (ligue "Selecionar" e arraste).', 'info'); return; }
    const linhas = [];
    for (let y = y1 + 1; y <= y2; y++) {
      const cols = [];
      for (let x = x1; x <= x2; x++) cols.push(String(valorDoPreenchimento(x, y1, y - y1) ?? '').replace(/[\t\n\r]+/g, ' '));
      linhas.push(cols.join('\t'));
    }
    tornarAtual();
    sheet.paste(x1, y1 + 1, linhas.join('\n'));
    sheet.updateSelectionFromCoords(x1, y1, x2, y2);
  });

  botao('Apagar', 'Limpar o conteúdo das células selecionadas', () => {
    if (!exigirFaixa() || !sheet.highlighted || !sheet.highlighted.length) return;
    tornarAtual();
    sheet.setValue(sheet.highlighted, '');
  });

  const dica = document.createElement('span');
  dica.className = 'pl-dica';
  dica.textContent = 'Toque para selecionar · toque de novo para editar';
  barra.appendChild(dica);
  ancoraDaBarra.parentNode.insertBefore(barra, ancoraDaBarra);

  return () => {
    container.removeEventListener('touchstart', aoTocar);
    container.removeEventListener('touchmove', aoMover);
    container.removeEventListener('touchend', aoSoltar);
    barra.remove();
  };
}
