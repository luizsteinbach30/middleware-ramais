import { api } from '/static/js/api.js';
import { toast } from '/static/js/components/toast.js';

const force = new URLSearchParams(location.search).get('force') === '1';
if (force) document.getElementById('acct-banner').classList.remove('hidden');

const cur = document.getElementById('pw-current');
const nw = document.getElementById('pw-new');
const cf = document.getElementById('pw-confirm');
const submit = document.getElementById('pw-submit');

function check() {
  const v = nw.value;
  const length = v.length >= 12;
  const letter = /[a-zA-Z]/.test(v);
  const digit = /\d/.test(v);
  const match = v && v === cf.value;
  const rules = document.getElementById('pw-rules');
  rules.querySelector('[data-rule="length"]').className = (length ? 'text-green-400' : 'text-gray-500') + ' flex items-center gap-1';
  rules.querySelector('[data-rule="letter"]').className = (letter ? 'text-green-400' : 'text-gray-500') + ' flex items-center gap-1';
  rules.querySelector('[data-rule="digit"]').className = (digit ? 'text-green-400' : 'text-gray-500') + ' flex items-center gap-1';
  submit.disabled = !(length && letter && digit && match && cur.value);
}

[cur, nw, cf].forEach((el) => el.addEventListener('input', check));

document.getElementById('pw-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await api('/api/auth/change-password', { method: 'POST', body: { current: cur.value, new_password: nw.value } });
    toast.success('Senha alterada.');
    setTimeout(() => location.href = '/', 800);
  } catch (e) { toast.error('Falha. Verifique a senha atual.'); }
});
