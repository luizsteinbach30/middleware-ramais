// API wrapper — every fetch goes through here so CSRF and auth are uniform.

function getCsrf() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

export async function api(path, { method = 'GET', body, signal } = {}) {
  const headers = { 'Accept': 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = getCsrf();

  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
      credentials: 'same-origin',
    });
  } catch (err) {
    throw Object.assign(new Error('network_error'), { cause: err });
  }
  if (res.status === 401) {
    if (location.pathname !== '/login') {
      location.href = '/login?next=' + encodeURIComponent(location.pathname);
    }
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_e) {}
    throw Object.assign(new Error(detail), { status: res.status });
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export function qs(params) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') usp.set(k, v);
  }
  const s = usp.toString();
  return s ? `?${s}` : '';
}
