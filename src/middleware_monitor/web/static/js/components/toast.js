// Minimal toast component matching the design palette.
const ROOT_ID = 'toast-root';

function root() {
  let el = document.getElementById(ROOT_ID);
  if (!el) {
    el = document.createElement('div');
    el.id = ROOT_ID;
    el.className = 'fixed top-4 right-4 z-50 space-y-2';
    document.body.appendChild(el);
  }
  return el;
}

function show(message, tone = 'info') {
  const tones = {
    success: 'bg-green-500/15 text-green-300 ring-green-500/30',
    error: 'bg-red-500/15 text-red-300 ring-red-500/30',
    info: 'bg-blue-500/15 text-blue-300 ring-blue-500/30',
    warn: 'bg-yellow-500/15 text-yellow-300 ring-yellow-500/30',
  };
  const el = document.createElement('div');
  el.className = `pointer-events-auto rounded-lg ring-1 ring-inset px-3 py-2 text-xs shadow-lg ${tones[tone] || tones.info}`;
  el.textContent = message;
  root().appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 200ms'; }, 3800);
  setTimeout(() => el.remove(), 4100);
}

export const toast = {
  success: (m) => show(m, 'success'),
  error: (m) => show(m, 'error'),
  info: (m) => show(m, 'info'),
  warn: (m) => show(m, 'warn'),
};
