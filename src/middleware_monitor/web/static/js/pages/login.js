import { api } from '/static/js/api.js';

const form = document.getElementById('login-form');
const err = document.getElementById('login-error');
const togglePw = document.getElementById('toggle-pw');
const pw = document.getElementById('password');

togglePw?.addEventListener('click', () => {
  pw.type = pw.type === 'password' ? 'text' : 'password';
});

form?.addEventListener('submit', async (e) => {
  e.preventDefault();
  err.classList.add('hidden');
  const username = document.getElementById('username').value.trim();
  const password = pw.value;
  try {
    const data = await api('/api/auth/login', { method: 'POST', body: { username, password } });
    if (data.must_change_password) location.href = '/account?force=1';
    else location.href = new URLSearchParams(location.search).get('next') || '/';
  } catch (e) {
    err.textContent = e.status === 429 ? 'Muitas tentativas. Tente novamente em alguns minutos.' : 'Usuário ou senha inválidos.';
    err.classList.remove('hidden');
  }
});
