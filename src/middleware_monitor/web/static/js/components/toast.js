// Toast centralizado no topo da tela com slide-down + auto-dismiss.
// Visível em qualquer parte do app — basta `import { toast } from '/static/js/components/toast.js'`
// e chamar `toast.success("mensagem")`, `toast.error(...)`, etc.

const ROOT_ID = 'toast-root';
const STYLE_ID = 'toast-style';
const DURATION_MS = 3200;
const SLIDE_OUT_MS = 240;

function injectStyleOnce() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement('style');
  style.id = STYLE_ID;
  style.textContent = `
    #${ROOT_ID} {
      position: fixed;
      top: 16px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 9999;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      pointer-events: none;
      width: max-content;
      max-width: calc(100vw - 32px);
    }
    .ec-toast {
      pointer-events: auto;
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 12px 18px;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 500;
      line-height: 1.3;
      box-shadow:
        0 12px 32px -8px rgba(0,0,0,0.5),
        0 4px 12px -2px rgba(0,0,0,0.3);
      backdrop-filter: blur(6px);
      ring: 1px solid;
      transform: translateY(-12px);
      opacity: 0;
      animation: ec-toast-in 260ms cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
      max-width: 520px;
      white-space: normal;
      text-align: center;
    }
    .ec-toast.ec-toast-out {
      animation: ec-toast-out ${SLIDE_OUT_MS}ms ease-in forwards;
    }
    .ec-toast-success {
      background: rgba(34, 197, 94, 0.18);
      color: #bbf7d0;
      box-shadow:
        0 12px 32px -8px rgba(34, 197, 94, 0.25),
        0 4px 12px -2px rgba(0,0,0,0.3),
        inset 0 0 0 1px rgba(34, 197, 94, 0.45);
    }
    .ec-toast-error {
      background: rgba(239, 68, 68, 0.18);
      color: #fecaca;
      box-shadow:
        0 12px 32px -8px rgba(239, 68, 68, 0.25),
        0 4px 12px -2px rgba(0,0,0,0.3),
        inset 0 0 0 1px rgba(239, 68, 68, 0.45);
    }
    .ec-toast-info {
      background: rgba(59, 130, 246, 0.18);
      color: #bfdbfe;
      box-shadow:
        0 12px 32px -8px rgba(59, 130, 246, 0.25),
        0 4px 12px -2px rgba(0,0,0,0.3),
        inset 0 0 0 1px rgba(59, 130, 246, 0.45);
    }
    .ec-toast-warn {
      background: rgba(234, 179, 8, 0.18);
      color: #fef08a;
      box-shadow:
        0 12px 32px -8px rgba(234, 179, 8, 0.25),
        0 4px 12px -2px rgba(0,0,0,0.3),
        inset 0 0 0 1px rgba(234, 179, 8, 0.45);
    }
    .ec-toast-icon {
      flex-shrink: 0;
      width: 18px;
      height: 18px;
    }
    @keyframes ec-toast-in {
      0%   { transform: translateY(-16px); opacity: 0; }
      60%  { transform: translateY(2px);   opacity: 1; }
      100% { transform: translateY(0);     opacity: 1; }
    }
    @keyframes ec-toast-out {
      0%   { transform: translateY(0);     opacity: 1; }
      100% { transform: translateY(-12px); opacity: 0; }
    }
  `;
  document.head.appendChild(style);
}

function root() {
  let el = document.getElementById(ROOT_ID);
  if (!el) {
    el = document.createElement('div');
    el.id = ROOT_ID;
    document.body.appendChild(el);
  }
  return el;
}

const ICONS = {
  success: '<svg class="ec-toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z" clip-rule="evenodd"/></svg>',
  error:   '<svg class="ec-toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5z" clip-rule="evenodd"/></svg>',
  info:    '<svg class="ec-toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a.75.75 0 000 1.5h.253a.25.25 0 01.244.304l-.459 2.066A1.75 1.75 0 0010.747 15H11a.75.75 0 000-1.5h-.253a.25.25 0 01-.244-.304l.459-2.066A1.75 1.75 0 009.253 9H9z" clip-rule="evenodd"/></svg>',
  warn:    '<svg class="ec-toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v4a.75.75 0 01-1.5 0v-4A.75.75 0 0110 5zm0 8a.75.75 0 100 1.5.75.75 0 000-1.5z" clip-rule="evenodd"/></svg>',
};

function show(message, tone = 'info') {
  injectStyleOnce();
  const el = document.createElement('div');
  el.className = `ec-toast ec-toast-${tone}`;
  const icon = ICONS[tone] || ICONS.info;
  el.innerHTML = `${icon}<span>${String(message ?? '').replace(/[<>&]/g, (c) => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</span>`;
  root().appendChild(el);

  // Click to dismiss
  el.addEventListener('click', () => dismiss(el));

  // Auto dismiss
  setTimeout(() => dismiss(el), DURATION_MS);
}

function dismiss(el) {
  if (!el.isConnected || el.classList.contains('ec-toast-out')) return;
  el.classList.add('ec-toast-out');
  setTimeout(() => el.remove(), SLIDE_OUT_MS + 60);
}

export const toast = {
  success: (m) => show(m, 'success'),
  error:   (m) => show(m, 'error'),
  info:    (m) => show(m, 'info'),
  warn:    (m) => show(m, 'warn'),
};
