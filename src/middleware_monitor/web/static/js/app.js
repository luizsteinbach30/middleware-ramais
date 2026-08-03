// Bootstraps shared UI: icons, sidebar logout, degraded banner poll.
import { injectIcons } from '/static/js/components/icons.js';
import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

// Sidebar recolhível: o estado inicial é aplicado no <html> por um script
// inline no base.html (antes do paint); aqui só ficam o toggle e o atalho.
const SIDEBAR_KEY = 'mm.sidebar';

function setSidebarCollapsed(collapsed) {
  document.documentElement.classList.toggle('sidebar-collapsed', collapsed);
  try {
    localStorage.setItem(SIDEBAR_KEY, collapsed ? 'collapsed' : 'expanded');
  } catch (_e) { /* localStorage bloqueado: vale só nesta página */ }
  const btn = document.getElementById('sb-toggle');
  if (btn) {
    const label = collapsed ? 'Expandir menu (Ctrl+B)' : 'Recolher menu (Ctrl+B)';
    btn.title = collapsed ? `${label} — a planilha ganha espaço quando recolhido` : label;
    btn.setAttribute('aria-label', label);
  }
  // O Jspreadsheet mede a largura do container no render: sem avisar, a
  // planilha continua com a largura antiga depois de recolher o menu.
  window.dispatchEvent(new Event('resize'));
}

function bindSidebar() {
  const btn = document.getElementById('sb-toggle');
  const collapsed = document.documentElement.classList.contains('sidebar-collapsed');
  setSidebarCollapsed(collapsed);   // sincroniza título/aria com o estado salvo
  if (btn) {
    btn.addEventListener('click', () => {
      setSidebarCollapsed(!document.documentElement.classList.contains('sidebar-collapsed'));
    });
  }
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b') {
      e.preventDefault();
      setSidebarCollapsed(!document.documentElement.classList.contains('sidebar-collapsed'));
    }
  });
}

function bind() {
  injectIcons();
  bindSidebar();
  document.querySelectorAll('[data-action="logout"]').forEach((b) =>
    b.addEventListener('click', async () => {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch (_e) {}
      location.href = '/login';
    })
  );
}

async function pollReady() {
  try {
    const data = await api('/api/system/readyz');
    const banner = document.getElementById('degraded-banner');
    if (!banner) return;
    if (data.status === 'ok') banner.classList.add('hidden');
    else {
      banner.classList.remove('hidden');
      const t = document.getElementById('degraded-banner-text');
      if (t) t.textContent = 'Serviço degradado · ' + (data.reasons || []).join(', ');
    }
  } catch (_e) {}
}

document.addEventListener('DOMContentLoaded', () => {
  bind();
  pollReady();
  setInterval(pollReady, 30000);
});

window.toast = toast;
