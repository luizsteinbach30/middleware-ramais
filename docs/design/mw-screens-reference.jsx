/* global React */
// mw-screens.jsx — Middleware USCall Monitor v2.0
// All 10 screens + shell. Single file to keep things compact.
//
// Aesthetic: TELAS.md dark Tailwind base, with the polish vocabulary from the
// Configurador de Ramais (sumário lateral, file preview, fieldsets-like cards,
// switch component, validation chips, sticky save bar).

const { useState, useEffect, useMemo, useRef } = React;

// ─────────────── Mock data ───────────────

const NOW = new Date('2026-05-09T14:33:21');

const DEVICES = (() => {
  const names = ['3660','3661','3662','3663','3664','3665','3666','3667','3668','3669','3670','3671','3672','3680','3681','3682','3690','3691','3692','3700','3701','3702','3703','3710'];
  const models = ['Yealink T31', 'Yealink T23', 'Intelbras V3501', 'Grandstream GXP1610', 'Fanvil X3SP', '-'];
  return names.map((n, i) => {
    const offline = [3, 9, 14, 17].includes(i);
    const lUnav = [11, 22].includes(i);
    return {
      id: i + 1,
      name: n,
      ip: `10.20.30.${40 + i}`,
      mac: i % 7 === 0 ? '-' : `aa:bb:${(0x10 + i).toString(16).padStart(2, '0')}:cc:dd:ee`,
      model: models[i % models.length],
      logical: lUnav ? 'indisponivel' : 'disponivel',
      network: offline ? 'offline' : 'online',
      latency: offline ? null : (2 + (i % 7) + Math.round(Math.random()*4)),
      lastSeen: '14:33:21',
      lastPing: offline ? '13:50:14' : '14:33:18',
    };
  });
})();

const SUMMARY = {
  total: DEVICES.length,
  network_online: DEVICES.filter((d) => d.network === 'online').length,
  network_offline: DEVICES.filter((d) => d.network === 'offline').length,
  logical_available: DEVICES.filter((d) => d.logical === 'disponivel').length,
  logical_unavailable: DEVICES.filter((d) => d.logical === 'indisponivel').length,
  avg_latency_ms: 4,
  max_latency_ms: 25,
  webhooks_24h: 144,
  webhooks_24h_ok: 138,
  webhooks_24h_fail: 6,
  last_collection_at: '2026-05-09 14:33:21',
};

const COLLECTIONS = Array.from({ length: 18 }).map((_, i) => {
  const t = new Date(NOW.getTime() - i * 30 * 60000);
  return {
    id: 1000 - i,
    type: i % 6 === 5 ? 'devices' : 'extensions',
    collected_at: t.toISOString().replace('T', ' ').slice(0, 19),
    size_kb: 142 + (i * 3) % 40,
    hash: 'a1b2c3' + (1000 - i).toString(16),
  };
});

const SAMPLE_PAYLOAD = {
  type: 'extensions',
  collected_at: '2026-05-09T14:30:01Z',
  count: 24,
  ramais: [
    { ramal: '3660', status: 'disponivel', ip: '10.20.30.40', mac: 'aa:bb:10:cc:dd:ee' },
    { ramal: '3661', status: 'disponivel', ip: '10.20.30.41', mac: 'aa:bb:11:cc:dd:ee' },
    { ramal: '3662', status: 'disponivel', ip: '10.20.30.42', mac: '-' },
    { ramal: '3663', status: 'disponivel', ip: '10.20.30.43', mac: 'aa:bb:13:cc:dd:ee' },
    { ramal: '3671', status: 'indisponivel', ip: '10.20.30.51', mac: 'aa:bb:17:cc:dd:ee' },
  ],
};

const WEBHOOK_LOGS = Array.from({ length: 16 }).map((_, i) => {
  const t = new Date(NOW.getTime() - i * 7 * 60000);
  const types = ['extensions', 'devices', 'results', 'test'];
  const success = ![3, 9, 12].includes(i);
  return {
    id: 5000 - i,
    timestamp: t.toISOString().replace('T', ' ').slice(0, 19),
    type: types[i % types.length],
    http_status: success ? 200 : (i === 3 ? 502 : i === 9 ? 0 : 401),
    duration_ms: success ? 80 + (i * 11) % 220 : 4900 + (i * 23) % 100,
    success,
    attempt: success ? '1/3' : (i === 3 ? '2/3' : '3/3'),
    is_test: i % 4 === 3,
  };
});

const SYSTEM_LOGS = [
  { ts: '2026-05-09 14:33:21', lvl: 'INFO', mod: 'collector', msg: 'Coleta extensions concluída em 312ms (24 ramais).' },
  { ts: '2026-05-09 14:33:18', lvl: 'INFO', mod: 'monitor', msg: 'Ciclo de ping concluído: 22 online / 2 offline.' },
  { ts: '2026-05-09 14:33:01', lvl: 'WARN', mod: 'webhook', msg: 'Tentativa 2/3 falhou: timeout (extensions).' },
  { ts: '2026-05-09 14:30:01', lvl: 'INFO', mod: 'collector', msg: 'Snapshot persistido (id=1000, hash=a1b2c3).' },
  { ts: '2026-05-09 14:00:00', lvl: 'INFO', mod: 'updater', msg: 'Verificando releases (canal=stable).' },
  { ts: '2026-05-09 13:58:42', lvl: 'ERROR', mod: 'monitor', msg: 'Falha de ping em 10.20.30.51 (timeout > 1000ms).' },
  { ts: '2026-05-09 13:30:10', lvl: 'INFO', mod: 'scheduler', msg: 'Job collect_extensions disparado.' },
  { ts: '2026-05-09 12:00:00', lvl: 'DEBUG', mod: 'auth', msg: 'Sessão renovada (user=admin).' },
  { ts: '2026-05-09 09:14:33', lvl: 'WARN', mod: 'updater', msg: 'GitHub API rate-limit headers próximos do limite (60/h).' },
  { ts: '2026-05-09 08:00:00', lvl: 'INFO', mod: 'retention', msg: 'Job retention removeu 142 webhook_events e 30 device_pings.' },
];

const UPDATE_HISTORY = [
  { ts: '2026-04-22 10:14', from: '2.0.2', to: '2.0.3', channel: 'stable', status: 'success', dur: 38 },
  { ts: '2026-04-08 09:55', from: '2.0.1', to: '2.0.2', channel: 'stable', status: 'success', dur: 41 },
  { ts: '2026-03-29 12:10', from: '2.0.1-beta', to: '2.0.1', channel: 'stable', status: 'rolled_back', dur: 92, error: 'alembic upgrade head: tabela device_pings já existe' },
  { ts: '2026-03-19 22:00', from: '2.0.0', to: '2.0.1', channel: 'stable', status: 'success', dur: 35 },
];

// timeseries (24h, 96 buckets of 15 min)
const TIMESERIES = (() => {
  const arr = [];
  for (let i = 0; i < 96; i++) {
    const offBase = 6 + Math.sin(i / 5) * 2;
    const off = Math.max(0, Math.round(offBase + (i > 70 && i < 76 ? 4 : 0) + (Math.random() - 0.5)));
    arr.push({ online: 24 - off, offline: off });
  }
  return arr;
})();

// device latency history (60 points)
const DEVICE_LATENCY = (() => {
  const arr = [];
  for (let i = 0; i < 60; i++) {
    const base = 4 + Math.sin(i / 8) * 1.2;
    const drop = i === 32 || i === 33;
    arr.push({ ms: drop ? null : Math.round((base + Math.random() * 1.8) * 10) / 10, online: !drop });
  }
  return arr;
})();

// ─────────────── Icons ───────────────

function I({ n, s = 16, cls = '' }) {
  const p = { width: s, height: s, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', className: cls };
  switch (n) {
    case 'home': return <svg {...p}><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>;
    case 'phone': return <svg {...p}><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>;
    case 'database': return <svg {...p}><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v6a9 3 0 0 0 18 0V5"/><path d="M3 11v6a9 3 0 0 0 18 0v-6"/></svg>;
    case 'webhook': return <svg {...p}><path d="M18 16.98h-5.99c-1.1 0-1.95.94-2.48 1.9A4 4 0 1 1 8.05 13.8"/><path d="M11 8c1.1 0 1.95-.94 2.48-1.9A4 4 0 1 1 16 12"/><path d="M7.41 14.66l-2.5 4.33A4 4 0 1 1 8 16"/></svg>;
    case 'list': return <svg {...p}><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>;
    case 'settings': return <svg {...p}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>;
    case 'download': return <svg {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>;
    case 'refresh': return <svg {...p}><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/><path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/></svg>;
    case 'eye': return <svg {...p}><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>;
    case 'eye-off': return <svg {...p}><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>;
    case 'check': return <svg {...p}><polyline points="20 6 9 17 4 12"/></svg>;
    case 'x': return <svg {...p}><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>;
    case 'alert': return <svg {...p}><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>;
    case 'arrow-left': return <svg {...p}><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>;
    case 'play': return <svg {...p}><polygon points="5 3 19 12 5 21 5 3"/></svg>;
    case 'log-out': return <svg {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>;
    case 'package': return <svg {...p}><line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>;
    case 'user': return <svg {...p}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>;
    case 'activity': return <svg {...p}><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>;
    case 'shield': return <svg {...p}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>;
    case 'menu': return <svg {...p}><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>;
    case 'chevron-r': return <svg {...p}><polyline points="9 18 15 12 9 6"/></svg>;
    case 'copy': return <svg {...p}><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>;
    case 'edit': return <svg {...p}><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>;
    case 'play-circle': return <svg {...p}><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/></svg>;
    default: return null;
  }
}

// ─────────────── Reusable bits ───────────────

function Badge({ tone = 'gray', children, dot }) {
  const map = {
    green: 'bg-green-500/15 text-green-400 ring-green-500/30',
    red: 'bg-red-500/15 text-red-400 ring-red-500/30',
    blue: 'bg-blue-500/15 text-blue-400 ring-blue-500/30',
    yellow: 'bg-yellow-500/15 text-yellow-400 ring-yellow-500/30',
    gray: 'bg-gray-500/15 text-gray-400 ring-gray-500/30',
    indigo: 'bg-indigo-500/15 text-indigo-300 ring-indigo-500/30',
  };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset ${map[tone]}`}>
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${{
        green: 'bg-green-400', red: 'bg-red-400', blue: 'bg-blue-400', yellow: 'bg-yellow-400', gray: 'bg-gray-400', indigo: 'bg-indigo-300',
      }[tone]}`} />}
      {children}
    </span>
  );
}

function Toggle({ checked, onChange, label }) {
  return (
    <label className="inline-flex items-center gap-3 cursor-pointer select-none">
      <span className="relative inline-block w-11 h-6">
        <input type="checkbox" className="sr-only peer" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="absolute inset-0 rounded-full bg-gray-700 peer-checked:bg-blue-500 transition-colors"></span>
        <span className="absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform peer-checked:translate-x-5"></span>
      </span>
      {label && <span className="text-sm text-gray-200">{label}</span>}
    </label>
  );
}

function Btn({ tone = 'primary', size = 'md', icon, children, onClick, disabled, type = 'button', cls = '' }) {
  const sizes = { sm: 'px-2.5 py-1.5 text-xs', md: 'px-3.5 py-2 text-sm', lg: 'px-4 py-2.5 text-sm' };
  const tones = {
    primary: 'bg-blue-500 hover:bg-blue-400 text-white shadow-sm',
    danger: 'bg-red-500 hover:bg-red-400 text-white',
    success: 'bg-green-500 hover:bg-green-400 text-white',
    ghost: 'bg-transparent hover:bg-gray-700/60 text-gray-200 ring-1 ring-inset ring-gray-700',
    subtle: 'bg-gray-800 hover:bg-gray-700 text-gray-200 ring-1 ring-inset ring-gray-700',
  };
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`inline-flex items-center gap-2 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${sizes[size]} ${tones[tone]} ${cls}`}>
      {icon && <I n={icon} s={size === 'sm' ? 12 : 14} />}
      {children}
    </button>
  );
}

function Card({ children, cls = '', pad = 'p-5' }) {
  return <div className={`bg-gray-800 ring-1 ring-gray-700 rounded-xl ${pad} ${cls}`}>{children}</div>;
}

function Field({ label, hint, error, required, children, full }) {
  return (
    <label className={`flex flex-col gap-1.5 ${full ? 'md:col-span-2' : ''}`}>
      <span className="text-xs font-semibold text-gray-300 flex items-center gap-2">
        {label}{required && <span className="text-red-400">*</span>}
        {hint && <span className="font-normal text-gray-500">{hint}</span>}
      </span>
      {children}
      {error && <span className="text-xs text-red-400 flex items-center gap-1"><I n="alert" s={11}/> {error}</span>}
    </label>
  );
}

function Input(props) {
  const { invalid, ...rest } = props;
  return <input {...rest} className={`bg-gray-900 ring-1 ring-inset ring-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 ${invalid ? 'ring-red-500' : ''} ${rest.disabled ? 'opacity-60 cursor-not-allowed' : ''} ${rest.className || ''}`} />;
}

function Select(props) {
  return <select {...props} className={`bg-gray-900 ring-1 ring-inset ring-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 ${props.className || ''}`} />;
}

function MaskedInput({ value, onChange, placeholder = '••••••••' }) {
  const [editing, setEditing] = useState(false);
  if (!editing) {
    return (
      <div className="flex gap-2">
        <Input value={value ? '••••••••••••' : ''} disabled placeholder={placeholder} className="flex-1 font-mono"/>
        <Btn tone="subtle" size="md" icon="edit" onClick={() => { setEditing(true); onChange(''); }}>Alterar</Btn>
      </div>
    );
  }
  return (
    <div className="flex gap-2">
      <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder="Cole o novo valor" className="flex-1 font-mono" autoFocus/>
      <Btn tone="ghost" size="md" onClick={() => setEditing(false)}>Cancelar</Btn>
    </div>
  );
}

// ─────────────── SVG mini-charts ───────────────

function MultiLineChart({ data, w = 760, h = 200 }) {
  const max = Math.max(...data.map((p) => p.online + p.offline));
  const stepX = w / (data.length - 1);
  const path = (key, color) => {
    let d = '';
    data.forEach((p, i) => {
      const x = i * stepX;
      const y = h - (p[key] / max) * (h - 24) - 12;
      d += (i === 0 ? 'M' : 'L') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    });
    return <path d={d} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>;
  };
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-full">
      {[0.25, 0.5, 0.75].map((g) => <line key={g} x1="0" x2={w} y1={h * g} y2={h * g} stroke="rgba(148,163,184,0.12)" strokeDasharray="3 4"/>)}
      {path('online', '#34d399')}
      {path('offline', '#f87171')}
    </svg>
  );
}

function LatencyChart({ data, w = 800, h = 220 }) {
  const points = data.filter((p) => p.ms != null);
  const max = Math.max(...points.map((p) => p.ms)) * 1.2;
  const min = 0;
  const stepX = w / (data.length - 1);
  let d = '';
  let area = '';
  data.forEach((p, i) => {
    const x = i * stepX;
    if (p.ms == null) { d += ' M' + x + ' ' + (h - 8); return; }
    const y = h - ((p.ms - min) / (max - min)) * (h - 24) - 12;
    d += (d ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    area += (area ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
  });
  area += `L${w} ${h} L0 ${h} Z`;
  const offlineMarks = data.map((p, i) => p.ms == null ? <circle key={i} cx={i * stepX} cy={h - 8} r="3" fill="#f87171"/> : null);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-full">
      <defs>
        <linearGradient id="latgrad" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#60a5fa" stopOpacity="0.35"/>
          <stop offset="100%" stopColor="#60a5fa" stopOpacity="0"/>
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75].map((g) => <line key={g} x1="0" x2={w} y1={h * g} y2={h * g} stroke="rgba(148,163,184,0.12)" strokeDasharray="3 4"/>)}
      <path d={area} fill="url(#latgrad)"/>
      <path d={d} fill="none" stroke="#60a5fa" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
      {offlineMarks}
    </svg>
  );
}

// ─────────────── Sidebar + Topbar ───────────────

const NAV = [
  { id: 'dashboard',   label: 'Dashboard',     icon: 'home' },
  { id: 'devices',     label: 'Devices',       icon: 'phone' },
  { id: 'collections', label: 'Coletas',       icon: 'database' },
  { id: 'webhooks',    label: 'Webhook logs',  icon: 'webhook' },
  { id: 'logs',        label: 'Logs',          icon: 'list' },
  { id: 'config',      label: 'Configuração',  icon: 'settings' },
  { id: 'updates',     label: 'Atualizações',  icon: 'package' },
];

function Sidebar({ route, onGo, version, onLogout }) {
  return (
    <aside className="hidden md:flex flex-col w-64 shrink-0 bg-gray-950 border-r border-gray-800 h-screen sticky top-0">
      <div className="px-5 py-5 border-b border-gray-800 flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-blue-500/15 ring-1 ring-blue-500/40 grid place-items-center text-blue-400">
          <I n="activity" s={18}/>
        </div>
        <div>
          <div className="text-sm font-semibold text-gray-100 leading-tight">USCall Monitor</div>
          <div className="text-[10px] tracking-wider uppercase text-gray-500">Middleware v{version}</div>
        </div>
      </div>
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-auto">
        {NAV.map((n) => (
          <button key={n.id} onClick={() => onGo(n.id)} className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${route === n.id ? 'bg-blue-500/10 text-blue-300 ring-1 ring-blue-500/30' : 'text-gray-400 hover:text-gray-100 hover:bg-gray-800/60'}`}>
            <I n={n.icon} s={16}/>
            {n.label}
          </button>
        ))}
      </nav>
      <div className="px-3 py-3 border-t border-gray-800 space-y-1">
        <button onClick={() => onGo('account')} className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${route === 'account' ? 'bg-blue-500/10 text-blue-300' : 'text-gray-400 hover:text-gray-100 hover:bg-gray-800/60'}`}>
          <I n="user" s={16}/>
          <span className="flex-1 text-left">admin</span>
          <span className="text-[10px] uppercase tracking-wider text-gray-600">role</span>
        </button>
        <button onClick={onLogout} className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-gray-400 hover:text-red-300 hover:bg-red-500/10">
          <I n="log-out" s={16}/>Sair
        </button>
      </div>
    </aside>
  );
}

function Header({ title, subtitle, actions, banner }) {
  return (
    <div className="border-b border-gray-800 bg-gray-900/80 backdrop-blur sticky top-0 z-20">
      {banner}
      <div className="px-6 py-4 flex items-center gap-4">
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-semibold text-gray-100 truncate">{title}</h1>
          {subtitle && <p className="text-xs text-gray-400 mt-0.5 truncate">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-2">{actions}</div>
      </div>
    </div>
  );
}

// ─────────────── 0. Login ───────────────

function LoginScreen({ onLogin }) {
  const [u, setU] = useState('admin');
  const [p, setP] = useState('');
  const [show, setShow] = useState(false);
  const [err, setErr] = useState('');

  const submit = (e) => {
    e.preventDefault();
    if (!u || !p) { setErr('Informe usuário e senha.'); return; }
    if (p.length < 4) { setErr('Usuário ou senha inválidos.'); return; }
    onLogin();
  };

  return (
    <div className="min-h-screen grid place-items-center bg-gradient-to-br from-gray-950 via-gray-900 to-gray-950 px-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center text-center mb-8">
          <div className="w-12 h-12 rounded-xl bg-blue-500/15 ring-1 ring-blue-500/40 grid place-items-center text-blue-400 mb-3">
            <I n="activity" s={24}/>
          </div>
          <h1 className="text-xl font-semibold text-gray-100">Middleware Monitor</h1>
          <p className="text-xs text-gray-500 mt-1">Entre para acessar o painel local</p>
        </div>
        <Card pad="p-6">
          <form onSubmit={submit} className="space-y-4">
            <Field label="Usuário" required>
              <Input autoFocus value={u} onChange={(e) => setU(e.target.value)} placeholder="admin"/>
            </Field>
            <Field label="Senha" required>
              <div className="relative">
                <Input type={show ? 'text' : 'password'} value={p} onChange={(e) => setP(e.target.value)} placeholder="••••••••" className="pr-10 w-full"/>
                <button type="button" onClick={() => setShow((s) => !s)} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-200 p-1.5"><I n={show ? 'eye-off' : 'eye'} s={16}/></button>
              </div>
            </Field>
            {err && (
              <div className="rounded-lg bg-red-500/10 ring-1 ring-red-500/30 px-3 py-2 text-xs text-red-300 flex items-center gap-2">
                <I n="alert" s={14}/>{err}
              </div>
            )}
            <Btn type="submit" tone="primary" cls="w-full justify-center">Entrar</Btn>
            <button type="button" className="w-full text-xs text-gray-500 hover:text-gray-300 text-center">Esqueci minha senha</button>
          </form>
        </Card>
        <div className="text-center text-xs text-gray-600 mt-6">v2.0.3 · canal stable</div>
      </div>
    </div>
  );
}

// ─────────────── 1. Dashboard ───────────────

function Dashboard({ onGo }) {
  const KPIs = [
    { label: 'Devices', value: SUMMARY.total, hint: 'Total monitorado', tone: 'gray' },
    { label: 'Rede online', value: SUMMARY.network_online, hint: `${SUMMARY.network_online}/${SUMMARY.total} respondendo ping`, tone: 'green' },
    { label: 'Rede offline', value: SUMMARY.network_offline, hint: 'Sem resposta no último ciclo', tone: 'red' },
    { label: 'Lóg. disponíveis', value: SUMMARY.logical_available, hint: 'Status USCall', tone: 'blue' },
    { label: 'Lóg. indisponíveis', value: SUMMARY.logical_unavailable, hint: 'USCall reportou falha', tone: 'yellow' },
    { label: 'Latência média', value: SUMMARY.avg_latency_ms + ' ms', hint: 'Máxima ' + SUMMARY.max_latency_ms + ' ms', tone: 'indigo' },
  ];
  return (
    <>
      <Header
        title="Dashboard operacional"
        subtitle={`Última coleta às ${SUMMARY.last_collection_at} · ${SUMMARY.total} ramais`}
        actions={<>
          <Btn tone="ghost" icon="refresh" size="sm">Atualizar</Btn>
          <Btn tone="primary" icon="play" size="sm">Forçar coleta</Btn>
        </>}
      />
      <div className="px-6 py-6 space-y-6">
        <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
          {KPIs.map((k) => (
            <Card key={k.label} pad="p-4" cls="hover:ring-gray-600 transition">
              <div className="text-[11px] uppercase tracking-wider text-gray-400 font-semibold">{k.label}</div>
              <div className={`text-2xl font-bold mt-1 ${{green:'text-green-400',red:'text-red-400',blue:'text-blue-400',yellow:'text-yellow-400',indigo:'text-indigo-300',gray:'text-gray-100'}[k.tone]}`}>{k.value}</div>
              <div className="text-xs text-gray-500 mt-1">{k.hint}</div>
            </Card>
          ))}
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <Card cls="xl:col-span-2">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-semibold text-gray-100">Online vs offline · 24h</h3>
                <p className="text-xs text-gray-500 mt-0.5">Agrupamento por 15 min · 96 buckets</p>
              </div>
              <div className="flex items-center gap-3 text-xs">
                <span className="flex items-center gap-1.5 text-gray-300"><span className="w-2.5 h-2.5 rounded-full bg-green-400"/>Online</span>
                <span className="flex items-center gap-1.5 text-gray-300"><span className="w-2.5 h-2.5 rounded-full bg-red-400"/>Offline</span>
              </div>
            </div>
            <div className="h-56"><MultiLineChart data={TIMESERIES}/></div>
            <div className="grid grid-cols-4 gap-3 mt-4 text-xs">
              <div className="px-3 py-2 rounded-lg bg-gray-900/60"><div className="text-gray-500">00:00</div><div className="text-gray-200 font-semibold mt-0.5">23 / 1</div></div>
              <div className="px-3 py-2 rounded-lg bg-gray-900/60"><div className="text-gray-500">06:00</div><div className="text-gray-200 font-semibold mt-0.5">22 / 2</div></div>
              <div className="px-3 py-2 rounded-lg bg-gray-900/60"><div className="text-gray-500">12:00</div><div className="text-gray-200 font-semibold mt-0.5">24 / 0</div></div>
              <div className="px-3 py-2 rounded-lg bg-gray-900/60 ring-1 ring-yellow-500/20"><div className="text-yellow-300/80">17:30</div><div className="text-yellow-200 font-semibold mt-0.5">19 / 5</div></div>
            </div>
          </Card>

          <div className="space-y-6">
            <Card>
              <h3 className="text-sm font-semibold text-gray-100 mb-3 flex items-center gap-2"><I n="webhook" s={14}/> Webhooks 24h</h3>
              <div className="text-3xl font-bold text-gray-100">{SUMMARY.webhooks_24h}</div>
              <div className="flex items-center gap-2 mt-2 text-xs">
                <Badge tone="green" dot>{SUMMARY.webhooks_24h_ok} ok</Badge>
                <Badge tone="red" dot>{SUMMARY.webhooks_24h_fail} falha</Badge>
              </div>
              <div className="mt-3 h-2 rounded-full bg-gray-900 overflow-hidden flex">
                <div className="h-full bg-green-500" style={{ width: (SUMMARY.webhooks_24h_ok / SUMMARY.webhooks_24h * 100) + '%' }}/>
                <div className="h-full bg-red-500" style={{ width: (SUMMARY.webhooks_24h_fail / SUMMARY.webhooks_24h * 100) + '%' }}/>
              </div>
              <button onClick={() => onGo('webhooks')} className="text-xs text-blue-400 hover:text-blue-300 mt-3 inline-flex items-center gap-1">Ver logs <I n="chevron-r" s={12}/></button>
            </Card>

            <Card>
              <h3 className="text-sm font-semibold text-gray-100 mb-3 flex items-center gap-2"><I n="package" s={14}/> Sistema</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-gray-500">Versão</span><span className="text-gray-200 font-medium">2.0.3 <span className="text-gray-500">(stable)</span></span></div>
                <div className="flex justify-between"><span className="text-gray-500">Próxima</span><Badge tone="blue" dot>2.1.0 disponível</Badge></div>
                <div className="flex justify-between"><span className="text-gray-500">Último check</span><span className="text-gray-300">14:00:00</span></div>
                <div className="flex justify-between"><span className="text-gray-500">Auto-update</span><Badge tone="green">Ativado</Badge></div>
              </div>
              <button onClick={() => onGo('updates')} className="text-xs text-blue-400 hover:text-blue-300 mt-3 inline-flex items-center gap-1">Gerenciar <I n="chevron-r" s={12}/></button>
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}

// ─────────────── 2. Devices list ───────────────

function DevicesList({ onPick }) {
  const [search, setSearch] = useState('');
  const [netFilter, setNetFilter] = useState('all');
  const [logFilter, setLogFilter] = useState('all');
  const filtered = DEVICES.filter((d) => {
    if (search && !`${d.name} ${d.ip}`.toLowerCase().includes(search.toLowerCase())) return false;
    if (netFilter !== 'all' && d.network !== netFilter) return false;
    if (logFilter !== 'all' && d.logical !== logFilter) return false;
    return true;
  });

  return (
    <>
      <Header
        title="Devices"
        subtitle={`${filtered.length} de ${DEVICES.length} ramais`}
        actions={<>
          <Btn tone="ghost" icon="download" size="sm">Exportar CSV</Btn>
          <Btn tone="primary" icon="play" size="sm">Forçar ping</Btn>
        </>}
      />
      <div className="px-6 py-6 space-y-4">
        <Card pad="p-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <Field label="Buscar" hint="ramal ou IP">
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Ex: 3660 ou 10.20.30"/>
            </Field>
            <Field label="Status de rede">
              <Select value={netFilter} onChange={(e) => setNetFilter(e.target.value)}>
                <option value="all">Todos</option>
                <option value="online">Online</option>
                <option value="offline">Offline</option>
                <option value="unknown">Desconhecido</option>
              </Select>
            </Field>
            <Field label="Status lógico">
              <Select value={logFilter} onChange={(e) => setLogFilter(e.target.value)}>
                <option value="all">Todos</option>
                <option value="disponivel">Disponível</option>
                <option value="indisponivel">Indisponível</option>
              </Select>
            </Field>
            <div className="flex items-end gap-2">
              <Btn tone="ghost" size="md" onClick={() => { setSearch(''); setNetFilter('all'); setLogFilter('all'); }}>Limpar</Btn>
            </div>
          </div>
          <div className="flex flex-wrap gap-2 mt-4 pt-4 border-t border-gray-700/50">
            <Badge tone="blue" dot>USCall {SUMMARY.logical_available} disponíveis</Badge>
            <Badge tone="yellow" dot>{SUMMARY.logical_unavailable} indisponíveis</Badge>
            <Badge tone="green" dot>Rede {SUMMARY.network_online} online</Badge>
            <Badge tone="red" dot>{SUMMARY.network_offline} offline</Badge>
          </div>
        </Card>

        <Card pad="p-0" cls="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-900/60 text-gray-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="text-left px-4 py-3 font-semibold">Ramal</th>
                  <th className="text-left px-4 py-3 font-semibold">IP</th>
                  <th className="text-left px-4 py-3 font-semibold">MAC</th>
                  <th className="text-left px-4 py-3 font-semibold">Modelo</th>
                  <th className="text-left px-4 py-3 font-semibold">USCall</th>
                  <th className="text-left px-4 py-3 font-semibold">Rede</th>
                  <th className="text-right px-4 py-3 font-semibold">Lat.</th>
                  <th className="text-left px-4 py-3 font-semibold">Last seen</th>
                  <th className="text-right px-4 py-3 font-semibold">Ações</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800">
                {filtered.map((d) => (
                  <tr key={d.id} className="hover:bg-gray-700/30 transition cursor-pointer" onClick={() => onPick(d.id)}>
                    <td className="px-4 py-2.5 font-bold text-gray-100">{d.name}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-gray-300">{d.ip}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-gray-500">{d.mac}</td>
                    <td className="px-4 py-2.5 text-gray-400">{d.model}</td>
                    <td className="px-4 py-2.5">{d.logical === 'disponivel' ? <Badge tone="blue" dot>disponível</Badge> : <Badge tone="yellow" dot>indisponível</Badge>}</td>
                    <td className="px-4 py-2.5">{d.network === 'online' ? <Badge tone="green" dot>online</Badge> : <Badge tone="red" dot>offline</Badge>}</td>
                    <td className="px-4 py-2.5 text-right font-mono tabular-nums text-gray-300">{d.latency != null ? d.latency + ' ms' : '—'}</td>
                    <td className="px-4 py-2.5 text-gray-500 text-xs" title="UTC: 2026-05-09T17:33:21Z">{d.lastSeen}</td>
                    <td className="px-4 py-2.5 text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="inline-flex gap-1">
                        <button className="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100" title="Forçar ping"><I n="refresh" s={14}/></button>
                        <button className="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100" title="Detalhes" onClick={() => onPick(d.id)}><I n="chevron-r" s={14}/></button>
                      </div>
                    </td>
                  </tr>
                ))}
                {filtered.length === 0 && (
                  <tr><td colSpan="9" className="px-4 py-12 text-center text-sm text-gray-500">Nenhum device com esses filtros.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="px-4 py-3 border-t border-gray-800 flex items-center justify-between text-xs text-gray-500">
            <span>Página 1 de 1 · {filtered.length} resultados</span>
            <div className="flex gap-1">
              <Btn tone="ghost" size="sm" disabled>« Anterior</Btn>
              <Btn tone="ghost" size="sm" disabled>Próxima »</Btn>
            </div>
          </div>
        </Card>
      </div>
    </>
  );
}

// ─────────────── 3. Device detail ───────────────

function DeviceDetail({ id, onBack }) {
  const d = DEVICES.find((x) => x.id === id) || DEVICES[0];
  const [window, setWindow] = useState('24h');
  return (
    <>
      <Header
        title={`Ramal ${d.name}`}
        subtitle={`${d.ip} · ${d.model}`}
        actions={<>
          <Btn tone="ghost" icon="arrow-left" size="sm" onClick={onBack}>Voltar</Btn>
          <Btn tone="ghost" icon="edit" size="sm">Editar notas</Btn>
          <Btn tone="primary" icon="refresh" size="sm">Forçar ping</Btn>
        </>}
      />
      <div className="px-6 py-6 space-y-6">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-3">
          <Card pad="p-4">
            <div className="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Status lógico</div>
            <div className="mt-2">{d.logical === 'disponivel' ? <Badge tone="blue" dot>disponível</Badge> : <Badge tone="yellow" dot>indisponível</Badge>}</div>
            <div className="text-xs text-gray-500 mt-2">USCall reportou às 14:33:21</div>
          </Card>
          <Card pad="p-4">
            <div className="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Rede</div>
            <div className="mt-2">{d.network === 'online' ? <Badge tone="green" dot>online</Badge> : <Badge tone="red" dot>offline</Badge>}</div>
            <div className="text-xs text-gray-500 mt-2">Último ping {d.lastPing}</div>
          </Card>
          <Card pad="p-4">
            <div className="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Latência atual</div>
            <div className="text-2xl font-bold text-blue-300 mt-1 tabular-nums">{d.latency ?? '—'}<span className="text-sm text-gray-500 ml-1">ms</span></div>
            <div className="text-xs text-gray-500 mt-1">Média 24h: 4 ms</div>
          </Card>
          <Card pad="p-4">
            <div className="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">MAC</div>
            <div className="font-mono text-sm text-gray-200 mt-2">{d.mac}</div>
            <div className="text-xs text-gray-500 mt-1">Detectado por ARP</div>
          </Card>
        </div>

        <Card>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-gray-100">Histórico de latência</h3>
              <p className="text-xs text-gray-500 mt-0.5">2 quedas detectadas na janela atual</p>
            </div>
            <div className="inline-flex bg-gray-900 ring-1 ring-gray-700 rounded-lg p-0.5">
              {['24h', '7d', '30d', 'custom'].map((w) => (
                <button key={w} onClick={() => setWindow(w)} className={`px-3 py-1 text-xs font-medium rounded-md transition ${window === w ? 'bg-blue-500 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
                  {w === 'custom' ? 'Custom' : w}
                </button>
              ))}
            </div>
          </div>
          <div className="h-56"><LatencyChart data={DEVICE_LATENCY}/></div>
          <div className="flex items-center gap-4 text-xs text-gray-400 mt-3">
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-blue-400"/>Latência (ms)</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-red-400"/>Queda (offline)</span>
          </div>
        </Card>

        <Card pad="p-0" cls="overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800">
            <h3 className="text-sm font-semibold text-gray-100">Pings recentes</h3>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-900/60 text-gray-400 text-xs uppercase tracking-wider">
              <tr><th className="text-left px-4 py-2.5">Timestamp</th><th className="text-left px-4 py-2.5">Online</th><th className="text-right px-4 py-2.5">Latência</th></tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {[
                { ts: '14:33:18', on: true, ms: 3 },
                { ts: '14:32:18', on: true, ms: 4 },
                { ts: '14:31:18', on: true, ms: 4 },
                { ts: '14:30:18', on: false, ms: null },
                { ts: '14:29:18', on: false, ms: null },
                { ts: '14:28:18', on: true, ms: 5 },
                { ts: '14:27:18', on: true, ms: 4 },
              ].map((p, i) => (
                <tr key={i} className="hover:bg-gray-700/30">
                  <td className="px-4 py-2 text-gray-300 font-mono text-xs">{p.ts}</td>
                  <td className="px-4 py-2">{p.on ? <Badge tone="green" dot>online</Badge> : <Badge tone="red" dot>offline</Badge>}</td>
                  <td className="px-4 py-2 text-right font-mono tabular-nums text-gray-300">{p.ms != null ? p.ms + ' ms' : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}

// ─────────────── 4. Collections ───────────────

function CollectionsScreen() {
  const [selected, setSelected] = useState(COLLECTIONS[0].id);
  const [type, setType] = useState('all');
  const filtered = COLLECTIONS.filter((c) => type === 'all' || c.type === type);
  const sel = COLLECTIONS.find((c) => c.id === selected);
  return (
    <>
      <Header title="Histórico de coletas" subtitle={`${COLLECTIONS.length} snapshots persistidos`} actions={<>
        <Btn tone="ghost" icon="download" size="sm">Baixar JSON</Btn>
        <Btn tone="ghost" icon="copy" size="sm">Copiar</Btn>
      </>}/>
      <div className="px-6 py-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card pad="p-0" cls="overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800 flex items-center gap-3">
            <Select className="text-xs flex-shrink-0" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="all">Todos os tipos</option>
              <option value="extensions">extensions</option>
              <option value="devices">devices</option>
              <option value="results">results</option>
            </Select>
            <Input placeholder="Buscar por hash ou ID" className="flex-1 text-xs"/>
          </div>
          <div className="max-h-[640px] overflow-auto divide-y divide-gray-800">
            {filtered.map((c) => (
              <button key={c.id} onClick={() => setSelected(c.id)} className={`w-full text-left px-4 py-3 hover:bg-gray-700/30 transition ${selected === c.id ? 'bg-blue-500/10 ring-1 ring-inset ring-blue-500/30' : ''}`}>
                <div className="flex items-center justify-between">
                  <div className="font-mono text-xs text-gray-300">{c.collected_at}</div>
                  <Badge tone={c.type === 'extensions' ? 'blue' : 'indigo'}>{c.type}</Badge>
                </div>
                <div className="flex items-center justify-between mt-1.5">
                  <div className="text-xs text-gray-500">id #{c.id} · <span className="font-mono">{c.hash}</span></div>
                  <div className="text-xs text-gray-500 tabular-nums">{c.size_kb} KB</div>
                </div>
              </button>
            ))}
          </div>
          <div className="px-4 py-3 border-t border-gray-800 flex items-center justify-between text-xs text-gray-500">
            <span>1 / 1 · {filtered.length}</span>
            <div className="flex gap-1"><Btn tone="ghost" size="sm" disabled>«</Btn><Btn tone="ghost" size="sm" disabled>»</Btn></div>
          </div>
        </Card>

        <Card pad="p-0" cls="overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
            <div>
              <div className="text-sm font-semibold text-gray-100">Visualizador de payload</div>
              <div className="text-xs text-gray-500 font-mono mt-0.5">id #{sel.id} · {sel.collected_at}</div>
            </div>
            <Badge tone={sel.type === 'extensions' ? 'blue' : 'indigo'}>{sel.type}</Badge>
          </div>
          <pre className="bg-gray-950 px-4 py-4 text-xs leading-relaxed font-mono overflow-auto max-h-[600px]">
            <code>
              <span className="text-gray-500">{'{'}</span>{'\n'}
              {Object.entries(SAMPLE_PAYLOAD).map(([k, v]) => (
                <span key={k}>
                  {'  '}<span className="text-blue-300">"{k}"</span><span className="text-gray-400">: </span>
                  {typeof v === 'string' ? <span className="text-green-300">"{v}"</span>
                   : typeof v === 'number' ? <span className="text-yellow-300">{v}</span>
                   : Array.isArray(v) ? (
                       <>
                         <span className="text-gray-400">[</span>{'\n'}
                         {v.map((it, i) => (
                           <span key={i}>{'    '}<span className="text-gray-400">{'{'}</span>{Object.entries(it).map(([ik, iv], j, a) => (
                             <span key={ik}> <span className="text-blue-300">"{ik}"</span><span className="text-gray-400">: </span><span className="text-green-300">"{iv}"</span>{j < a.length - 1 ? <span className="text-gray-400">,</span> : null}</span>
                           ))} <span className="text-gray-400">{'}'}{i < v.length - 1 ? ',' : ''}</span>{'\n'}</span>
                         ))}
                         {'  '}<span className="text-gray-400">]</span>
                       </>
                     ) : null}
                  <span className="text-gray-400">,</span>{'\n'}
                </span>
              ))}
              <span className="text-gray-500">{'}'}</span>
            </code>
          </pre>
        </Card>
      </div>
    </>
  );
}

// ─────────────── 5. Webhook logs ───────────────

function WebhookLogs() {
  const [open, setOpen] = useState(null);
  return (
    <>
      <Header title="Webhook logs" subtitle="Histórico das chamadas enviadas pelo middleware" actions={<>
        <Btn tone="subtle" size="sm">Testar extensions</Btn>
        <Btn tone="subtle" size="sm">Testar devices</Btn>
        <Btn tone="subtle" size="sm">Testar results</Btn>
      </>}/>
      <div className="px-6 py-6 space-y-4">
        <Card pad="p-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <Field label="Tipo"><Select><option>Todos</option><option>extensions</option><option>devices</option><option>results</option><option>test</option></Select></Field>
            <Field label="Status"><Select><option>Todos</option><option>Sucesso</option><option>Falha</option></Select></Field>
            <Field label="De"><Input type="datetime-local" defaultValue="2026-05-09T00:00"/></Field>
            <Field label="Até"><Input type="datetime-local" defaultValue="2026-05-09T23:59"/></Field>
          </div>
        </Card>

        <Card pad="p-0" cls="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-900/60 text-gray-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left px-4 py-3 font-semibold">Data</th>
                <th className="text-left px-4 py-3 font-semibold">Tipo</th>
                <th className="text-right px-4 py-3 font-semibold">HTTP</th>
                <th className="text-right px-4 py-3 font-semibold">Tempo</th>
                <th className="text-left px-4 py-3 font-semibold">Status</th>
                <th className="text-left px-4 py-3 font-semibold">Tentativa</th>
                <th className="text-right px-4 py-3 font-semibold">Ações</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {WEBHOOK_LOGS.map((w) => {
                const httpTone = w.http_status === 0 ? 'gray' : w.http_status >= 500 ? 'red' : w.http_status >= 400 ? 'yellow' : 'green';
                return (
                  <tr key={w.id} className={`hover:bg-gray-700/30 ${w.is_test ? 'bg-indigo-500/5' : ''}`}>
                    <td className="px-4 py-2.5 font-mono text-xs text-gray-300">{w.timestamp}</td>
                    <td className="px-4 py-2.5">
                      <span className="inline-flex items-center gap-1.5">
                        <Badge tone={w.type === 'extensions' ? 'blue' : w.type === 'devices' ? 'green' : w.type === 'results' ? 'indigo' : 'yellow'}>{w.type}</Badge>
                        {w.is_test && <span className="text-[10px] uppercase tracking-wider text-indigo-300">test</span>}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-right"><Badge tone={httpTone}>{w.http_status || 'ERR'}</Badge></td>
                    <td className="px-4 py-2.5 text-right font-mono tabular-nums text-gray-300 text-xs">{w.duration_ms} ms</td>
                    <td className="px-4 py-2.5">{w.success ? <Badge tone="green" dot>Sucesso</Badge> : <Badge tone="red" dot>Falha</Badge>}</td>
                    <td className="px-4 py-2.5 text-xs text-gray-400 font-mono">{w.attempt}</td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="inline-flex gap-1">
                        <button onClick={() => setOpen(w)} className="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100" title="Ver payload"><I n="eye" s={14}/></button>
                        <button className="p-1.5 rounded text-gray-400 hover:bg-gray-700 hover:text-gray-100" title="Reenviar"><I n="refresh" s={14}/></button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      </div>

      {open && (
        <div className="fixed inset-0 bg-black/60 z-50 grid place-items-center px-4" onClick={() => setOpen(null)}>
          <div className="bg-gray-800 ring-1 ring-gray-700 rounded-xl max-w-2xl w-full" onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-gray-700 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-gray-100">Payload enviado</h3>
                <div className="text-xs text-gray-500 mt-0.5 font-mono">id #{open.id} · {open.timestamp}</div>
              </div>
              <button onClick={() => setOpen(null)} className="text-gray-400 hover:text-gray-100"><I n="x" s={18}/></button>
            </div>
            <pre className="bg-gray-950 px-5 py-4 text-xs font-mono text-gray-300 max-h-96 overflow-auto rounded-b-xl">
              {`{
  "type": "${open.type}",
  "test": ${open.is_test},
  "client_code": "acme",
  "timestamp": "${open.timestamp}",
  "ramais": [
    { "ramal": "3660", "status": "disponivel", "ip": "10.20.30.40" },
    { "ramal": "3661", "status": "disponivel", "ip": "10.20.30.41" }
  ]
}`}
            </pre>
          </div>
        </div>
      )}
    </>
  );
}

// ─────────────── 6. System logs ───────────────

function LogsScreen() {
  const [auto, setAuto] = useState(false);
  const [open, setOpen] = useState(null);
  const lvlMap = { DEBUG: 'gray', INFO: 'blue', WARN: 'yellow', ERROR: 'red' };
  return (
    <>
      <Header title="Logs do sistema" subtitle={`${SYSTEM_LOGS.length} entradas · retenção 14 dias`} actions={<>
        <div className="flex items-center gap-2 text-xs text-gray-300">Auto-refresh<Toggle checked={auto} onChange={setAuto}/></div>
      </>}/>
      <div className="px-6 py-6 space-y-4">
        <Card pad="p-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <Field label="Nível"><Select><option>Todos</option><option>DEBUG</option><option>INFO</option><option>WARN</option><option>ERROR</option></Select></Field>
            <Field label="Módulo"><Select><option>Todos</option><option>scheduler</option><option>collector</option><option>monitor</option><option>webhook</option><option>updater</option><option>auth</option></Select></Field>
            <Field label="Buscar"><Input placeholder="texto na mensagem"/></Field>
            <Field label="Período"><Input type="datetime-local" defaultValue="2026-05-09T00:00"/></Field>
          </div>
        </Card>

        <Card pad="p-0" cls="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-900/60 text-gray-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left px-4 py-3 font-semibold w-44">Timestamp</th>
                <th className="text-left px-4 py-3 font-semibold w-24">Nível</th>
                <th className="text-left px-4 py-3 font-semibold w-32">Módulo</th>
                <th className="text-left px-4 py-3 font-semibold">Mensagem</th>
                <th className="text-right px-4 py-3 font-semibold">Contexto</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {SYSTEM_LOGS.map((l, i) => (
                <tr key={i} className={`hover:bg-gray-700/30 ${l.lvl === 'ERROR' ? 'bg-red-500/5' : ''}`}>
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-300">{l.ts}</td>
                  <td className="px-4 py-2.5"><Badge tone={lvlMap[l.lvl]}>{l.lvl}</Badge></td>
                  <td className="px-4 py-2.5"><span className="px-2 py-0.5 rounded bg-gray-700/60 text-xs font-mono text-gray-300">{l.mod}</span></td>
                  <td className="px-4 py-2.5 text-gray-200">{l.msg}</td>
                  <td className="px-4 py-2.5 text-right"><button onClick={() => setOpen(l)} className="text-xs text-blue-400 hover:text-blue-300">ver contexto</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
      {open && (
        <div className="fixed inset-0 bg-black/60 z-50 grid place-items-center px-4" onClick={() => setOpen(null)}>
          <div className="bg-gray-800 ring-1 ring-gray-700 rounded-xl max-w-xl w-full" onClick={(e) => e.stopPropagation()}>
            <div className="px-5 py-4 border-b border-gray-700 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-100">Contexto · <span className="font-mono text-gray-400">{open.mod}</span></h3>
              <button onClick={() => setOpen(null)} className="text-gray-400 hover:text-gray-100"><I n="x" s={18}/></button>
            </div>
            <pre className="bg-gray-950 px-5 py-4 text-xs font-mono text-gray-300 rounded-b-xl">
{`{
  "trace_id": "a1b2c3d4-e5f6",
  "host": "uscall.empresa.com.br",
  "duration_ms": 312,
  "ramais_count": 24
}`}
            </pre>
          </div>
        </div>
      )}
    </>
  );
}

// ─────────────── 7. Config ───────────────

function ConfigScreen() {
  const [cfg, setCfg] = useState({
    client_code: 'acme-matriz',
    uscall_host: 'uscall.empresa.com.br',
    uscall_token: 'set',
    verify_ssl: true,
    extensions_interval_seconds: 30,
    devices_interval_seconds: 60,
    results_interval_seconds: 300,
    ping_timeout_ms: 1000,
    ping_concurrency: 20,
    device_ping_retention_days: 30,
    webhooks: {
      extensions: { enabled: true, url: 'https://base44.example.com/hook/ext', token: 'set', last: 'OK há 3min' },
      devices:    { enabled: true, url: 'https://base44.example.com/hook/dev', token: 'set', last: 'OK há 2min' },
      results:    { enabled: false, url: '', token: '', last: '—' },
    },
    webhook_log_retention_days: 30,
    collection_retention_days: 90,
    system_log_retention_days: 14,
  });
  const [dirty, setDirty] = useState(false);
  const [usCallTest, setUsCallTest] = useState(null);

  const set = (path, v) => {
    setDirty(true);
    setCfg((c) => {
      const n = JSON.parse(JSON.stringify(c));
      const ks = path.split('.');
      let o = n; for (let i = 0; i < ks.length - 1; i++) o = o[ks[i]];
      o[ks[ks.length - 1]] = v;
      return n;
    });
  };

  const runTest = () => {
    setUsCallTest({ loading: true });
    setTimeout(() => setUsCallTest({ ok: true, http: 200, latency: 142 }), 700);
  };

  return (
    <>
      <Header title="Configuração" subtitle="Parâmetros operacionais editáveis pela UI"
        actions={<>
          <Btn tone="ghost" size="sm" onClick={() => setDirty(false)}>Recarregar</Btn>
          <Btn tone="primary" size="sm" disabled={!dirty}>{dirty ? 'Salvar configuração' : 'Sem alterações'}</Btn>
        </>}
        banner={dirty && (
          <div className="px-6 py-2 bg-yellow-500/10 border-b border-yellow-500/30 text-xs text-yellow-300 flex items-center gap-2">
            <I n="alert" s={14}/> Você tem alterações não salvas.
          </div>
        )}
      />
      <div className="px-6 py-6 space-y-5 max-w-4xl">
        <Card>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-gray-100 flex items-center gap-2"><I n="shield" s={14}/> Identificação do cliente</h3>
              <p className="text-xs text-gray-500 mt-0.5">Slug usado no payload dos webhooks.</p>
            </div>
          </div>
          <div className="grid md:grid-cols-2 gap-4">
            <Field label="client_code" required hint="slug">
              <Input value={cfg.client_code} onChange={(e) => set('client_code', e.target.value)}/>
            </Field>
          </div>
        </Card>

        <Card>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-gray-100 flex items-center gap-2"><I n="activity" s={14}/> Integração USCall</h3>
              <p className="text-xs text-gray-500 mt-0.5">Endpoint que retorna o status dos ramais.</p>
            </div>
            <Btn tone="subtle" size="sm" icon="play-circle" onClick={runTest}>Testar conexão</Btn>
          </div>
          <div className="grid md:grid-cols-2 gap-4">
            <Field label="uscall_host" required hint="sem https://">
              <Input value={cfg.uscall_host} onChange={(e) => set('uscall_host', e.target.value)}/>
            </Field>
            <Field label="uscall_token" required hint="sensível">
              <MaskedInput value={cfg.uscall_token === 'set' ? '' : cfg.uscall_token} onChange={(v) => set('uscall_token', v)}/>
            </Field>
            <div className="md:col-span-2 flex items-center gap-3">
              <Toggle checked={cfg.verify_ssl} onChange={(v) => set('verify_ssl', v)} label="verify_ssl (recomendado: ligado)"/>
            </div>
            {usCallTest && (
              <div className={`md:col-span-2 rounded-lg px-3 py-2 text-xs flex items-center gap-2 ${usCallTest.loading ? 'bg-gray-700/50 text-gray-300' : usCallTest.ok ? 'bg-green-500/10 ring-1 ring-green-500/30 text-green-300' : 'bg-red-500/10 ring-1 ring-red-500/30 text-red-300'}`}>
                {usCallTest.loading ? <>Testando…</> : <><I n="check" s={14}/> Conexão OK · HTTP {usCallTest.http} · {usCallTest.latency} ms</>}
              </div>
            )}
          </div>
        </Card>

        <Card>
          <h3 className="text-sm font-semibold text-gray-100 mb-4">Intervalos de coleta <span className="text-xs font-normal text-gray-500">· em segundos, mínimo 10</span></h3>
          <div className="grid md:grid-cols-3 gap-4">
            <Field label="extensions" hint="USCall">
              <Input type="number" min="10" value={cfg.extensions_interval_seconds} onChange={(e) => set('extensions_interval_seconds', +e.target.value)}/>
            </Field>
            <Field label="devices" hint="ping/arp">
              <Input type="number" min="10" value={cfg.devices_interval_seconds} onChange={(e) => set('devices_interval_seconds', +e.target.value)}/>
            </Field>
            <Field label="results" hint="resultados de chamadas">
              <Input type="number" min="10" value={cfg.results_interval_seconds} onChange={(e) => set('results_interval_seconds', +e.target.value)}/>
            </Field>
          </div>
        </Card>

        <Card>
          <h3 className="text-sm font-semibold text-gray-100 mb-4">Monitoramento de rede</h3>
          <div className="grid md:grid-cols-3 gap-4">
            <Field label="ping_timeout_ms"><Input type="number" value={cfg.ping_timeout_ms} onChange={(e) => set('ping_timeout_ms', +e.target.value)}/></Field>
            <Field label="ping_concurrency" hint="máx 200"><Input type="number" min="1" max="200" value={cfg.ping_concurrency} onChange={(e) => set('ping_concurrency', +e.target.value)}/></Field>
            <Field label="device_ping_retention_days"><Input type="number" value={cfg.device_ping_retention_days} onChange={(e) => set('device_ping_retention_days', +e.target.value)}/></Field>
          </div>
        </Card>

        <Card>
          <h3 className="text-sm font-semibold text-gray-100 mb-4 flex items-center gap-2"><I n="webhook" s={14}/> Webhooks</h3>
          <div className="space-y-3">
            {Object.entries(cfg.webhooks).map(([k, w]) => (
              <div key={k} className="bg-gray-900/40 rounded-lg ring-1 ring-gray-700 p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-3">
                    <Badge tone={k === 'extensions' ? 'blue' : k === 'devices' ? 'green' : 'indigo'}>{k}</Badge>
                    <Toggle checked={w.enabled} onChange={(v) => set(`webhooks.${k}.enabled`, v)} label={w.enabled ? 'Habilitado' : 'Desligado'}/>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-gray-500">{w.last}</span>
                    <Btn tone="subtle" size="sm" icon="play-circle" disabled={!w.enabled}>Testar</Btn>
                  </div>
                </div>
                <div className="grid md:grid-cols-2 gap-3">
                  <Field label="url"><Input value={w.url} onChange={(e) => set(`webhooks.${k}.url`, e.target.value)} disabled={!w.enabled} placeholder="https://…"/></Field>
                  <Field label="token" hint="bearer"><MaskedInput value={w.token === 'set' ? '' : w.token} onChange={(v) => set(`webhooks.${k}.token`, v)}/></Field>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-gray-100">Retenção e limpeza</h3>
            <Btn tone="ghost" size="sm" icon="x">Limpar agora</Btn>
          </div>
          <div className="grid md:grid-cols-3 gap-4">
            <Field label="webhook_log_retention_days"><Input type="number" value={cfg.webhook_log_retention_days} onChange={(e) => set('webhook_log_retention_days', +e.target.value)}/></Field>
            <Field label="collection_retention_days"><Input type="number" value={cfg.collection_retention_days} onChange={(e) => set('collection_retention_days', +e.target.value)}/></Field>
            <Field label="system_log_retention_days"><Input type="number" value={cfg.system_log_retention_days} onChange={(e) => set('system_log_retention_days', +e.target.value)}/></Field>
          </div>
        </Card>
      </div>
    </>
  );
}

// ─────────────── 8. Updates ───────────────

function UpdatesScreen() {
  const [progress, setProgress] = useState(null);
  const [channel, setChannel] = useState('stable');
  const [auto, setAuto] = useState(true);

  const startUpdate = () => {
    setProgress({ stage: 'Baixando app-v2.1.0.tar.gz…', pct: 10 });
    const stages = [
      ['Verificando SHA256SUMS…', 35],
      ['Extraindo em app/2.1.0/…', 55],
      ['Rodando alembic upgrade head…', 75],
      ['Reiniciando serviço…', 92],
      ['Health-check OK', 100],
    ];
    let i = 0;
    const t = setInterval(() => {
      if (i >= stages.length) { clearInterval(t); setTimeout(() => setProgress(null), 1500); return; }
      setProgress({ stage: stages[i][0], pct: stages[i][1] });
      i++;
    }, 700);
  };

  return (
    <>
      <Header title="Atualizações" subtitle="Auto-update via GitHub Releases"/>
      <div className="px-6 py-6 space-y-6 max-w-4xl">
        <Card>
          <div className="grid md:grid-cols-2 gap-6">
            <div>
              <div className="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Versão atual</div>
              <div className="text-3xl font-bold text-gray-100 mt-1">2.0.3</div>
              <div className="flex items-center gap-2 mt-2">
                <Badge tone="green">stable</Badge>
                <span className="text-xs text-gray-500">Atualizada em 22/04/2026</span>
              </div>
              <div className="grid grid-cols-2 gap-3 mt-5">
                <div>
                  <div className="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Canal</div>
                  <Select className="mt-1.5 w-full" value={channel} onChange={(e) => setChannel(e.target.value)}>
                    <option value="stable">stable</option>
                    <option value="beta">beta</option>
                  </Select>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">Auto-update</div>
                  <div className="mt-2"><Toggle checked={auto} onChange={setAuto} label={auto ? 'Ativado' : 'Desligado'}/></div>
                </div>
              </div>
            </div>

            <div className="bg-gray-900/60 rounded-lg ring-1 ring-blue-500/30 p-4">
              <div className="flex items-center gap-2 text-xs text-blue-300 font-semibold uppercase tracking-wider"><I n="package" s={12}/> Nova versão disponível</div>
              <div className="text-2xl font-bold text-gray-100 mt-1">2.1.0</div>
              <div className="text-xs text-gray-400 mt-1">Publicada em 06/05/2026 · canal stable · 4.2 MB</div>
              <ul className="text-xs text-gray-300 mt-3 space-y-1">
                <li className="flex gap-2"><span className="text-green-400">+</span>Suporte a IPv6 nos coletores</li>
                <li className="flex gap-2"><span className="text-green-400">+</span>Endpoint Prometheus em /metrics</li>
                <li className="flex gap-2"><span className="text-blue-400">~</span>Retry de webhook com jitter</li>
                <li className="flex gap-2"><span className="text-yellow-400">!</span>Migration adicionando índice em device_pings</li>
              </ul>
              <div className="flex gap-2 mt-4">
                <Btn tone="subtle" size="sm" icon="refresh">Verificar agora</Btn>
                <Btn tone="primary" size="sm" icon="download" onClick={startUpdate}>Atualizar agora</Btn>
              </div>
            </div>
          </div>

          {progress && (
            <div className="mt-5 bg-gray-900/60 rounded-lg ring-1 ring-blue-500/30 p-4">
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-300 flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse"/>{progress.stage}</span>
                <span className="font-mono text-gray-400 tabular-nums">{progress.pct}%</span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-gray-800 overflow-hidden">
                <div className="h-full bg-blue-500 transition-all" style={{ width: progress.pct + '%' }}/>
              </div>
              <div className="text-[11px] text-gray-500 mt-2">Em caso de falha o serviço fará rollback automático para 2.0.3.</div>
            </div>
          )}
        </Card>

        <Card pad="p-0" cls="overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-800">
            <h3 className="text-sm font-semibold text-gray-100">Histórico de atualizações</h3>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-900/60 text-gray-400 text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left px-4 py-3 font-semibold">Data</th>
                <th className="text-left px-4 py-3 font-semibold">De</th>
                <th className="text-left px-4 py-3 font-semibold">Para</th>
                <th className="text-left px-4 py-3 font-semibold">Canal</th>
                <th className="text-left px-4 py-3 font-semibold">Status</th>
                <th className="text-right px-4 py-3 font-semibold">Duração</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {UPDATE_HISTORY.map((u, i) => (
                <tr key={i} className="hover:bg-gray-700/30">
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-300">{u.ts}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-400">{u.from}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-200 font-semibold">{u.to}</td>
                  <td className="px-4 py-2.5"><Badge tone={u.channel === 'beta' ? 'yellow' : 'gray'}>{u.channel}</Badge></td>
                  <td className="px-4 py-2.5">
                    {u.status === 'success' ? <Badge tone="green" dot>Sucesso</Badge>
                     : u.status === 'rolled_back' ? <Badge tone="yellow" dot>Rollback</Badge>
                     : <Badge tone="red" dot>Falha</Badge>}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono tabular-nums text-gray-300 text-xs">{u.dur}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </>
  );
}

// ─────────────── 9. Account ───────────────

function AccountScreen({ force, onLogout }) {
  const [old, setOld] = useState('');
  const [npw, setNpw] = useState('');
  const [conf, setConf] = useState('');
  const valid = npw.length >= 12 && /[a-zA-Z]/.test(npw) && /\d/.test(npw) && npw === conf;
  return (
    <>
      <Header title="Minha conta" subtitle={force ? 'Primeiro acesso — defina uma nova senha' : 'Gerencie sua sessão'}
        banner={force && (
          <div className="px-6 py-2 bg-yellow-500/10 border-b border-yellow-500/30 text-xs text-yellow-300 flex items-center gap-2">
            <I n="alert" s={14}/> Senha temporária em uso. Defina uma nova para continuar.
          </div>
        )}
      />
      <div className="px-6 py-6 grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-4xl">
        <Card>
          <h3 className="text-sm font-semibold text-gray-100 mb-1">Trocar senha</h3>
          <p className="text-xs text-gray-500 mb-4">Mínimo 12 caracteres, com letras e números.</p>
          <div className="space-y-3">
            <Field label="Senha atual" required><Input type="password" value={old} onChange={(e) => setOld(e.target.value)}/></Field>
            <Field label="Nova senha" required><Input type="password" value={npw} onChange={(e) => setNpw(e.target.value)}/></Field>
            <Field label="Confirmar nova senha" required error={conf && conf !== npw ? 'As senhas não conferem.' : undefined}>
              <Input type="password" value={conf} onChange={(e) => setConf(e.target.value)}/>
            </Field>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className={`flex items-center gap-1 ${npw.length >= 12 ? 'text-green-400' : 'text-gray-500'}`}><I n="check" s={12}/>≥ 12 caracteres</div>
              <div className={`flex items-center gap-1 ${/[a-zA-Z]/.test(npw) ? 'text-green-400' : 'text-gray-500'}`}><I n="check" s={12}/>letras</div>
              <div className={`flex items-center gap-1 ${/\d/.test(npw) ? 'text-green-400' : 'text-gray-500'}`}><I n="check" s={12}/>números</div>
            </div>
            <Btn tone="primary" disabled={!valid} cls="w-full justify-center mt-2">Trocar senha</Btn>
          </div>
        </Card>

        <div className="space-y-6">
          <Card>
            <h3 className="text-sm font-semibold text-gray-100 mb-3">Sessão atual</h3>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-gray-500">Usuário</span><span className="text-gray-200">admin</span></div>
              <div className="flex justify-between"><span className="text-gray-500">IP</span><span className="text-gray-200 font-mono text-xs">10.20.30.5</span></div>
              <div className="flex justify-between"><span className="text-gray-500">User-agent</span><span className="text-gray-300 text-xs">Chrome 124 · Win11</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Criada em</span><span className="text-gray-300 text-xs">14:00:32</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Expira em</span><span className="text-gray-300 text-xs">02:00:32</span></div>
            </div>
            <Btn tone="ghost" icon="log-out" cls="w-full justify-center mt-4" onClick={onLogout}>Sair desta sessão</Btn>
          </Card>
          <Card>
            <h3 className="text-sm font-semibold text-gray-100 mb-3 flex items-center gap-2"><I n="shield" s={14}/> Política de senha</h3>
            <ul className="text-xs text-gray-400 space-y-1.5">
              <li>• Hash com bcrypt cost 12.</li>
              <li>• Bloqueio após 5 tentativas em 10 min.</li>
              <li>• Sessão expira em 12h, renovação por atividade.</li>
              <li>• Cookies HttpOnly, SameSite=Lax, Secure em produção.</li>
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}

// ─────────────── 10. 404 ───────────────

function NotFound({ onGo }) {
  return (
    <div className="min-h-[80vh] grid place-items-center px-4">
      <Card cls="max-w-md text-center" pad="p-8">
        <div className="w-14 h-14 rounded-xl bg-gray-700/40 grid place-items-center mx-auto mb-4 text-gray-400"><I n="alert" s={28}/></div>
        <h2 className="text-xl font-semibold text-gray-100">Página não encontrada</h2>
        <p className="text-sm text-gray-500 mt-2">A rota solicitada não existe ou foi removida.</p>
        <Btn tone="primary" cls="mt-5" onClick={() => onGo('dashboard')}>Voltar ao dashboard</Btn>
      </Card>
    </div>
  );
}

// ─────────────── App shell ───────────────

function App() {
  const [authed, setAuthed] = useState(true);
  const [forcePw, setForcePw] = useState(false);
  const [route, setRoute] = useState('dashboard');
  const [deviceId, setDeviceId] = useState(null);
  const [showDegraded, setShowDegraded] = useState(false);

  // Tweaks
  const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
    "screen": "dashboard",
    "showDegraded": false,
    "forcePassword": false
  }/*EDITMODE-END*/;
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  useEffect(() => { if (t.screen && t.screen !== 'login' && t.screen !== route) setRoute(t.screen); }, [t.screen]);
  useEffect(() => { setShowDegraded(t.showDegraded); }, [t.showDegraded]);
  useEffect(() => { setForcePw(t.forcePassword); }, [t.forcePassword]);

  if (!authed) return (
    <>
      <LoginScreen onLogin={() => { setAuthed(true); setRoute('dashboard'); }}/>
      <TweaksPanel>
        <TweakSection label="Demo"/>
        <Btn tone="primary" size="sm" cls="w-full justify-center" onClick={() => setAuthed(true)}>Pular login</Btn>
      </TweaksPanel>
    </>
  );

  let content;
  if (route === 'dashboard')   content = <Dashboard onGo={(r) => { setRoute(r); setTweak('screen', r); }}/>;
  else if (route === 'devices')     content = <DevicesList onPick={(id) => { setDeviceId(id); setRoute('device-detail'); }}/>;
  else if (route === 'device-detail') content = <DeviceDetail id={deviceId} onBack={() => setRoute('devices')}/>;
  else if (route === 'collections') content = <CollectionsScreen/>;
  else if (route === 'webhooks')    content = <WebhookLogs/>;
  else if (route === 'logs')        content = <LogsScreen/>;
  else if (route === 'config')      content = <ConfigScreen/>;
  else if (route === 'updates')     content = <UpdatesScreen/>;
  else if (route === 'account')     content = <AccountScreen force={forcePw} onLogout={() => setAuthed(false)}/>;
  else if (route === 'notfound')    content = <NotFound onGo={(r) => setRoute(r)}/>;
  else content = <NotFound onGo={(r) => setRoute(r)}/>;

  const screenLabel = {
    dashboard: '01 Dashboard', devices: '02 Devices', 'device-detail': '03 Device detail', collections: '04 Collections',
    webhooks: '05 Webhook logs', logs: '06 Logs', config: '07 Config', updates: '08 Updates', account: '09 Account', notfound: '10 NotFound',
  }[route];

  return (
    <div className="min-h-screen bg-gray-900 text-gray-200 flex" data-screen-label={screenLabel}>
      <Sidebar route={route === 'device-detail' ? 'devices' : route} onGo={(r) => { setRoute(r); setTweak('screen', r); }} version="2.0.3" onLogout={() => setAuthed(false)}/>
      <div className="flex-1 min-w-0">
        {showDegraded && (
          <div className="px-6 py-2 bg-yellow-500/10 border-b border-yellow-500/30 text-xs text-yellow-200 flex items-center gap-2">
            <I n="alert" s={14}/> Serviço degradado — coleta pausada. Verifique <button onClick={() => setRoute('logs')} className="underline hover:text-yellow-100">/logs</button>.
          </div>
        )}
        {content}
      </div>

      <TweaksPanel>
        <TweakSection label="Navegação demo"/>
        <div className="grid grid-cols-2 gap-1.5">
          {['login','dashboard','devices','device-detail','collections','webhooks','logs','config','updates','account','notfound'].map((s) => (
            <button key={s} onClick={() => {
              if (s === 'login') { setAuthed(false); }
              else { setAuthed(true); setRoute(s); setTweak('screen', s); if (s === 'device-detail') setDeviceId(1); }
            }} className={`text-[11px] font-semibold px-2 py-1.5 rounded ring-1 ${route === s && authed ? 'bg-blue-500 text-white ring-blue-500' : 'bg-transparent ring-gray-700 text-gray-300 hover:bg-gray-700'}`}>
              {s}
            </button>
          ))}
        </div>
        <TweakSection label="Estados"/>
        <TweakToggle label="Banner serviço degradado" value={showDegraded} onChange={(v) => setTweak('showDegraded', v)}/>
        <TweakToggle label="Forçar troca de senha" value={forcePw} onChange={(v) => { setTweak('forcePassword', v); if (v) setRoute('account'); }}/>
      </TweaksPanel>
    </div>
  );
}

window.MWApp = App;
