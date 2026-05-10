// Lightweight SVG charts — no external deps.

export function multiLineChart(container, series) {
  const w = container.clientWidth || 760;
  const h = container.clientHeight || 200;
  const max = Math.max(1, ...series.flatMap((s) => s.data));
  const stepX = series[0]?.data.length > 1 ? w / (series[0].data.length - 1) : w;

  const path = (data) => data.map((v, i) => {
    const x = i * stepX;
    const y = h - (v / max) * (h - 24) - 12;
    return (i === 0 ? 'M' : 'L') + x.toFixed(1) + ' ' + y.toFixed(1);
  }).join(' ');

  const grid = [0.25, 0.5, 0.75]
    .map((g) => `<line x1="0" x2="${w}" y1="${h*g}" y2="${h*g}" stroke="rgba(148,163,184,0.12)" stroke-dasharray="3 4"/>`)
    .join('');
  const lines = series.map((s) => `<path d="${path(s.data)}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`).join('');

  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="w-full h-full">${grid}${lines}</svg>`;
}

export function latencyChart(container, points /* [{ms, online}] */) {
  const w = container.clientWidth || 800;
  const h = container.clientHeight || 220;
  const valid = points.filter((p) => p.ms != null);
  const max = (valid.length ? Math.max(...valid.map((p) => p.ms)) : 1) * 1.2 || 1;
  const stepX = points.length > 1 ? w / (points.length - 1) : w;
  let d = '';
  let area = '';
  points.forEach((p, i) => {
    const x = i * stepX;
    if (p.ms == null) return;
    const y = h - (p.ms / max) * (h - 24) - 12;
    d += (d ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    area += (area ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
  });
  if (area) area += `L${w} ${h} L0 ${h} Z`;
  const offlineMarks = points.map((p, i) => p.ms == null ? `<circle cx="${i * stepX}" cy="${h - 8}" r="3" fill="#f87171"/>` : '').join('');
  const grid = [0.25, 0.5, 0.75]
    .map((g) => `<line x1="0" x2="${w}" y1="${h*g}" y2="${h*g}" stroke="rgba(148,163,184,0.12)" stroke-dasharray="3 4"/>`)
    .join('');
  container.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="w-full h-full">
    <defs><linearGradient id="latgrad" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#60a5fa" stop-opacity="0.35"/><stop offset="100%" stop-color="#60a5fa" stop-opacity="0"/></linearGradient></defs>
    ${grid}
    <path d="${area}" fill="url(#latgrad)"/>
    <path d="${d}" fill="none" stroke="#60a5fa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    ${offlineMarks}
  </svg>`;
}
