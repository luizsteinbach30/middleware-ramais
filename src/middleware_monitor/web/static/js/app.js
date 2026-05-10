// Bootstraps shared UI: icons, sidebar logout, degraded banner poll.
import { injectIcons } from '/static/js/components/icons.js';
import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

function bind() {
  injectIcons();
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
