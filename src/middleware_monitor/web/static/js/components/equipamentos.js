// Peças comuns às telas de equipamentos (/equipamentos e /equipamentos/<id>).
// Só apresentação: os estados, tipos e frentes vêm da API; aqui só se decide
// como cada um se lê e com que cor (as mesmas do resto do middleware).
import { fmtTs } from '/static/js/util/datetime.js';

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const TONS = {
  green: 'bg-green-500/15 text-green-400 ring-green-500/30',
  red: 'bg-red-500/15 text-red-400 ring-red-500/30',
  yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30',
  blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30',
  gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30',
};

export function badge(tom, texto, { ponto = true, titulo = '' } = {}) {
  return `<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset whitespace-nowrap ${TONS[tom] || TONS.gray}"${titulo ? ` title="${esc(titulo)}"` : ''}>${ponto ? '<span class="w-1.5 h-1.5 rounded-full bg-current"></span>' : ''}${esc(texto)}</span>`;
}

// Estado do equipamento (CONTRATO §13.4).
export const ESTADOS = {
  no_ar: ['green', 'no ar'],
  degradado: ['yellow', 'com problema'],
  fora: ['red', 'fora'],
  desconhecido: ['gray', 'desconhecido'],
};

export function badgeEstado(estado) {
  const [tom, texto] = ESTADOS[estado] || ['gray', estado || '—'];
  return badge(tom, texto);
}

// Estado de uma verificação.
export const ESTADOS_DA_VERIFICACAO = {
  ok: ['green', 'ok'],
  falha: ['red', 'falha'],
  desconhecido: ['gray', 'desconhecido'],
  pausada: ['gray', 'pausada'],
};

export function badgeVerificacao(estado, titulo = '') {
  const [tom, texto] = ESTADOS_DA_VERIFICACAO[estado] || ['gray', estado || '—'];
  return badge(tom, texto, { titulo });
}

export const TIPOS = {
  gravador: 'Gravador',
  camera: 'Câmera',
  switch: 'Switch',
  roteador: 'Roteador',
  firewall: 'Firewall',
  ponto_de_acesso: 'Ponto de acesso',
  link: 'Link',
  nobreak: 'Nobreak',
  servidor: 'Servidor',
  hipervisor: 'Hipervisor',
  controle_de_acesso: 'Controle de acesso',
  impressora: 'Impressora',
  outro: 'Outro',
};

export const FRENTES = { cftv: 'CFTV', rede: 'Rede', servidores: 'Servidores', outros: 'Outros' };

export const TIPOS_DE_VERIFICACAO = { icmp: 'Ping', tcp: 'Porta TCP', http: 'Página HTTP', cftv: 'Leitura do gravador' };

// A API local manda UTC com "Z"; o resultado do "Verificar agora" e a leitura do
// gravador vêm na forma do lote (UTC sem fuso). As duas viram hora local.
export function utc(valor) {
  if (!valor || typeof valor !== 'string') return valor;
  return /[zZ]$|[+-]\d\d:?\d\d$/.test(valor) ? valor : `${valor}Z`;
}

export function quando(valor, fallback = '—') {
  return valor ? fmtTs(utc(valor)) : fallback;
}

export function parametros(tipo, p = {}) {
  if (tipo === 'icmp') return '—';
  if (tipo === 'tcp') return `porta ${p.porta ?? '—'}`;
  if (tipo === 'http') {
    const cert = p.esquema === 'https' ? (p.verificarCertificado ? ' · confere certificado' : ' · sem conferir certificado') : '';
    return `${p.esquema || 'http'} · porta ${p.porta ?? '—'} · ${p.caminho || '/'} · espera ${p.statusEsperado ?? 200}${cert}`;
  }
  if (tipo === 'cftv') return `${p.adaptador || '—'} · ${p.esquema || 'http'} · porta ${p.porta ?? '—'}`;
  const pares = Object.entries(p || {});
  return pares.length ? pares.map(([k, v]) => `${k}=${v}`).join(' · ') : '—';
}

// --- Leitura do gravador (o `cftv` do retrato, CONTRATO §13.9) ------------------------------

const VIDEO = { ok: ['green', 'vídeo'], sem_video: ['red', 'sem vídeo'], desconhecido: ['gray', 'vídeo ?'] };
const DISCO = { ok: ['green', 'ok'], defeito: ['red', 'com defeito'], cheio: ['yellow', 'cheio'], ausente: ['gray', 'ausente'] };
const LIMITE_DE_DESVIO_S = 120;

function desvio(s) {
  if (s === null || s === undefined) return '—';
  const abs = Math.abs(s);
  if (abs <= 5) return 'certa';
  const quanto = abs >= 120 ? `${Math.round(abs / 60)} min` : `${abs} s`;
  // desvioS = hora do gravador − hora desta máquina.
  return `${quanto} ${s > 0 ? 'adiantada' : 'atrasada'}`;
}

export function leituraDoGravador(c) {
  const canais = c.canais || [];
  const discos = c.discos || [];
  const tileCanal = (k) => {
    const [tv, v] = VIDEO[k.video] || VIDEO.desconhecido;
    const grav = k.gravando === true ? badge('green', 'gravando', { ponto: false })
      : k.gravando === false ? badge('red', 'não grava', { ponto: false }) : badge('gray', 'gravação ?', { ponto: false });
    const ruim = k.video === 'sem_video' || k.gravando === false;
    return `<div class="rounded-lg p-2.5 ring-1 ${ruim ? 'bg-red-500/5 ring-red-500/30' : 'bg-gray-900/60 ring-gray-700'}">
      <div class="flex items-baseline justify-between gap-2"><span class="text-xs font-semibold text-gray-200 tabular-nums">${esc(k.numero)}</span><span class="text-[11px] text-gray-500 truncate">${esc(k.nome || '')}</span></div>
      <div class="flex flex-wrap gap-1 mt-1.5">${badge(tv, v, { ponto: false })}${grav}</div>
    </div>`;
  };
  const linhaDisco = (d) => {
    const [td, txt] = DISCO[d.estado] || ['gray', d.estado || '—'];
    const total = Number(d.totalGb) || 0;
    const livre = d.livreGb === null || d.livreGb === undefined ? null : Number(d.livreGb);
    const pct = total && livre !== null ? Math.max(0, Math.min(100, Math.round(((total - livre) / total) * 100))) : null;
    const cor = pct === null ? '#6b7280' : pct >= 95 ? '#f87171' : pct >= 85 ? '#facc15' : '#4ade80';
    return `<li class="py-2">
      <div class="flex items-center justify-between gap-3 text-sm">
        <span class="text-gray-200">Disco ${esc(d.numero)}</span>
        <span class="flex items-center gap-2">${pct === null ? '' : `<span class="text-xs text-gray-400 tabular-nums">${(total - livre).toFixed(1)} de ${total.toFixed(1)} GB · ${pct}%</span>`}${badge(td, txt)}</span>
      </div>
      <div class="mm-barra mt-1.5"><span style="width:${pct ?? 0}%;background:${cor}"></span></div>
    </li>`;
  };
  const horaErrada = c.desvioS !== null && c.desvioS !== undefined && Math.abs(c.desvioS) > LIMITE_DE_DESVIO_S;
  return `
    <div class="grid md:grid-cols-3 gap-4 text-sm">
      <div><div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Hora do gravador</div><div class="text-gray-200 mt-1">${quando(c.horaDoGravador)}</div></div>
      <div><div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Desvio</div><div class="mt-1 ${horaErrada ? 'text-yellow-400' : 'text-gray-200'}">${esc(desvio(c.desvioS))}</div></div>
      <div><div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Dias guardados</div><div class="text-gray-200 mt-1">${c.diasGuardados ?? '—'}</div></div>
    </div>
    <div>
      <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold mb-2">Canais <span class="normal-case tracking-normal font-normal text-gray-600">· ${canais.length}</span></div>
      ${canais.length ? `<div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">${canais.map(tileCanal).join('')}</div>` : '<div class="text-xs text-gray-500">O gravador não informou canais.</div>'}
    </div>
    <div>
      <div class="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Discos</div>
      ${discos.length ? `<ul class="divide-y divide-gray-700/60">${discos.map(linhaDisco).join('')}</ul>` : '<div class="text-xs text-gray-500 mt-2">Nenhum disco no gravador.</div>'}
    </div>`;
}
