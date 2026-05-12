// Helpers para formatar timestamps vindos da API no fuso local do navegador.
//
// A API sempre devolve ISO-8601 em UTC com sufixo "Z" (ex.: "2026-05-12T16:41:02Z").
// Como o backend só conhece UTC, é o navegador que precisa converter para o
// fuso do operador — caso contrário a tabela mostraria o horário UTC literal
// e o usuário veria 16:41 quando o relógio dele marca 13:41 (Brasília).

const LOCALE = 'pt-BR';
const FULL_OPTS = {
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
  hour12: false,
};
const TIME_OPTS = {
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
};

function _parse(value) {
  if (value === null || value === undefined || value === '') return null;
  const d = new Date(value);
  return isNaN(d.getTime()) ? null : d;
}

export function fmtTs(value, { fallback = '—' } = {}) {
  const d = _parse(value);
  return d ? d.toLocaleString(LOCALE, FULL_OPTS) : (typeof value === 'string' ? value : fallback);
}

export function fmtTime(value, { fallback = '—' } = {}) {
  const d = _parse(value);
  return d ? d.toLocaleTimeString(LOCALE, TIME_OPTS) : fallback;
}
