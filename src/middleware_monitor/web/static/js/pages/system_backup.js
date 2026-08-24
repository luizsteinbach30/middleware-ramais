import { api } from '/static/js/api.js';
import { injectIcons } from '/static/js/components/icons.js';
import { toast } from '/static/js/components/toast.js';
import { fmtTs } from '/static/js/util/datetime.js';

const $ = (s) => document.getElementById(s);

const SECOES = {
  config: 'Configurações do sistema',
  environments: 'Ambientes do Configurador',
  users: 'Usuários',
  devices: 'Devices monitorados',
};

// Pacote lido pelo "Analisar": guardado para o botão de restaurar não precisar
// pedir o arquivo e a passphrase de novo.
let pacote = null;

function fmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function baixarArquivo(conteudo, nome, tipo) {
  const url = URL.createObjectURL(new Blob([conteudo], { type: tipo }));
  const a = document.createElement('a');
  a.href = url;
  a.download = nome;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function secoesMarcadas(container) {
  return [...container.querySelectorAll('input[type=checkbox]:checked')].map((c) => c.value);
}

// ------------------------------------------------------------- exportar

async function exportar() {
  const sections = secoesMarcadas($('bk-export-sections'));
  const passphrase = $('bk-export-pass').value;
  if (!sections.length) { toast.error('Escolha ao menos uma seção'); return; }
  if (!passphrase) { toast.error('Informe uma passphrase'); return; }
  const btn = $('bk-export');
  btn.disabled = true;
  try {
    const envelope = await api('/api/backup/export', {
      method: 'POST', body: { passphrase, sections },
    });
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
    baixarArquivo(JSON.stringify(envelope), `middleware-${stamp}.mwrbak`, 'application/json');
    $('bk-export-pass').value = '';
    toast.success('Configuração exportada (cifrada)');
  } catch (e) {
    toast.error('Falha ao exportar: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------- importar

async function analisar() {
  const file = $('bk-import-file').files[0];
  const passphrase = $('bk-import-pass').value;
  if (!file) { toast.error('Selecione um arquivo .mwrbak'); return; }
  if (!passphrase) { toast.error('Informe a passphrase'); return; }
  const btn = $('bk-inspect');
  btn.disabled = true;
  try {
    const blob = await file.text();
    const resumo = await api('/api/backup/inspect', { method: 'POST', body: { blob, passphrase } });
    pacote = { blob, passphrase, resumo };
    renderPreview(resumo);
    toast.success('Arquivo lido');
  } catch (e) {
    pacote = null;
    $('bk-import-preview').classList.add('hidden');
    toast.error('Não foi possível ler: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

function descreveSecao(chave, dados) {
  if (chave === 'config') {
    return `${dados.chaves} chave(s), ${dados.servidores_uscall} servidor(es) USCall, ${dados.brokers_mqtt} broker(s) MQTT`;
  }
  if (chave === 'environments') {
    const nomes = (dados.nomes || []).join(', ');
    return `${dados.ambientes} ambiente(s), ${dados.linhas} linha(s)${nomes ? ` — ${nomes}` : ''}`;
  }
  if (chave === 'users') return `${dados.usuarios} usuário(s)`;
  if (chave === 'devices') return `${dados.devices} device(s)`;
  return '';
}

function renderPreview(resumo) {
  const secoes = resumo.sections || {};
  $('bk-import-summary').innerHTML = `
    <div>Gerado em <strong class="text-gray-100">${fmtTs(resumo.generated_at)}</strong>
    pela versão <strong class="text-gray-100">${resumo.app_version || '—'}</strong>.</div>`;
  $('bk-import-sections').innerHTML = Object.entries(secoes).map(([chave, dados]) => `
    <label class="flex items-start gap-2.5 text-sm text-gray-300">
      <input type="checkbox" value="${chave}" checked class="mt-0.5 accent-blue-500">
      <span>${SECOES[chave] || chave}<span class="block text-[11px] text-gray-500">${descreveSecao(chave, dados)}</span></span>
    </label>`).join('') || '<div class="text-xs text-gray-500">O arquivo não traz nenhuma seção.</div>';
  $('bk-import-preview').classList.remove('hidden');
}

async function importar() {
  if (!pacote) { toast.error('Analise o arquivo primeiro'); return; }
  const sections = secoesMarcadas($('bk-import-sections'));
  const mode = document.querySelector('input[name=bk-mode]:checked').value;
  if (!sections.length) { toast.error('Escolha ao menos uma seção'); return; }
  if (mode === 'replace') {
    const alvos = sections.filter((s) => s === 'environments' || s === 'config');
    if (alvos.length && !confirm(
      'Substituir apaga os ambientes atuais e o histórico de aplicação deles, '
      + 'além dos servidores USCall e brokers MQTT, recriando tudo a partir do arquivo. Continuar?',
    )) return;
  }
  const btn = $('bk-import');
  btn.disabled = true;
  try {
    const r = await api('/api/backup/import', {
      method: 'POST',
      body: { blob: pacote.blob, passphrase: pacote.passphrase, sections, mode },
    });
    const partes = Object.entries(r.applied).map(([k, v]) => `${SECOES[k] || k}: ${Object.entries(v).map(([a, b]) => `${b} ${a}`).join(', ')}`);
    toast.success('Configuração restaurada — ' + partes.join(' · '));
  } catch (e) {
    toast.error('Falha ao restaurar: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------ snapshots

async function gerarSnapshot() {
  const btn = $('bk-snapshot');
  btn.disabled = true;
  try {
    const r = await api('/api/backup/snapshot', { method: 'POST' });
    const podado = r.pruned.length ? ` (${r.pruned.length} antigo(s) removido(s))` : '';
    toast.success(`Backup gerado: ${r.name} — ${fmtBytes(r.size_bytes)}${podado}`);
    await carregarArquivos();
  } catch (e) {
    toast.error('Falha ao gerar backup: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

const TIPO_LABEL = {
  snapshot: '<span class="text-blue-300">banco completo</span>',
  bundle: '<span class="text-green-300">configuração</span>',
  'pre-restore': '<span class="text-yellow-300">banco anterior</span>',
};

async function carregarArquivos() {
  const r = await api('/api/backup/files');
  $('bk-dir').textContent = r.dir;
  $('bk-files-total').textContent = r.files.length
    ? `${r.files.length} arquivo(s) · ${fmtBytes(r.total_bytes)}`
    : 'Nenhum backup gerado ainda.';
  $('bk-files').innerHTML = r.files.map((f) => `
    <tr class="hover:bg-gray-900/40">
      <td class="px-4 py-2.5 font-mono text-xs text-gray-200 break-all">${f.name}</td>
      <td class="px-4 py-2.5 text-xs">${TIPO_LABEL[f.kind] || f.kind}</td>
      <td class="px-4 py-2.5 text-right text-xs text-gray-400 tabular-nums">${fmtBytes(f.size_bytes)}</td>
      <td class="px-4 py-2.5 text-xs text-gray-400">${fmtTs(f.modified_at)}</td>
      <td class="px-4 py-2.5 text-right whitespace-nowrap">
        <a href="/api/backup/files/${encodeURIComponent(f.name)}" download
           class="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-300 hover:text-blue-300 hover:bg-blue-500/10">baixar</a>
        ${f.kind === 'bundle' ? '' : `<button data-restore="${f.name}"
           class="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-300 hover:text-yellow-300 hover:bg-yellow-500/10">restaurar</button>`}
        <button data-delete="${f.name}"
           class="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-gray-400 hover:text-red-300 hover:bg-red-500/10">apagar</button>
      </td>
    </tr>`).join('');

  $('bk-files').querySelectorAll('button[data-restore]').forEach((b) => {
    b.addEventListener('click', () => restaurar(b.dataset.restore));
  });
  $('bk-files').querySelectorAll('button[data-delete]').forEach((b) => {
    b.addEventListener('click', () => apagar(b.dataset.delete));
  });
}

async function restaurar(nome) {
  if (!confirm(
    `Restaurar o banco a partir de "${nome}"?\n\n`
    + 'A troca acontece na próxima inicialização do middleware — nada muda agora. '
    + 'Tudo o que foi gravado depois desse backup (coletas, chamadas, ledger, alterações '
    + 'de configuração) é perdido. O banco atual fica guardado como pre-restore-*.db.',
  )) return;
  try {
    await api('/api/backup/restore', { method: 'POST', body: { name: nome } });
    toast.warn('Restauração agendada — reinicie o middleware para concluir');
    await Promise.all([carregarPendente(), carregarArquivos()]);
  } catch (e) {
    toast.error('Falha ao agendar: ' + e.message);
  }
}

async function apagar(nome) {
  if (!confirm(`Apagar o backup "${nome}"? Não há como desfazer.`)) return;
  try {
    await api(`/api/backup/files/${encodeURIComponent(nome)}`, { method: 'DELETE' });
    toast.success('Backup apagado');
    await carregarArquivos();
  } catch (e) {
    toast.error('Falha ao apagar: ' + e.message);
  }
}

async function enviarParaRestaurar(file) {
  if (!confirm(
    `Restaurar o banco a partir de "${file.name}"?\n\n`
    + 'O arquivo é validado antes de ser aceito; a troca acontece na próxima '
    + 'inicialização do middleware.',
  )) { $('bk-upload').value = ''; return; }
  const fd = new FormData();
  fd.append('file', file);
  try {
    // FormData não passa pelo wrapper de API (que serializa JSON): fetch direto,
    // com o CSRF que o wrapper enviaria.
    const res = await fetch('/api/backup/restore/upload', {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '' },
    });
    if (!res.ok) {
      let detalhe = res.statusText;
      try { detalhe = (await res.json()).detail || detalhe; } catch (_e) { /* resposta sem json */ }
      throw new Error(detalhe);
    }
    toast.warn('Restauração agendada — reinicie o middleware para concluir');
    await carregarPendente();
  } catch (e) {
    toast.error('Falha ao enviar: ' + e.message);
  } finally {
    $('bk-upload').value = '';
  }
}

async function carregarPendente() {
  const { pending } = await api('/api/backup/restore');
  const box = $('bk-pending');
  if (!pending) { box.classList.add('hidden'); return; }
  $('bk-pending-src').textContent = pending.source || '—';
  const c = pending.counts || {};
  const partes = [];
  if (c.extension_environments != null) partes.push(`${c.extension_environments} ambiente(s)`);
  if (c.extension_lines != null) partes.push(`${c.extension_lines} linha(s)`);
  if (c.devices != null) partes.push(`${c.devices} device(s)`);
  $('bk-pending-counts').textContent = partes.length ? `(${partes.join(', ')})` : '';
  box.classList.remove('hidden');
}

async function cancelarPendente() {
  try {
    await api('/api/backup/restore', { method: 'DELETE' });
    toast.success('Restauração cancelada');
    await carregarPendente();
  } catch (e) {
    toast.error('Falha ao cancelar: ' + e.message);
  }
}

// ---------------------------------------------------------- agendamento

function descreveAgenda(cfg) {
  if (!cfg.auto_enabled) return 'Desligado — só backups manuais';
  const hh = String(cfg.hour).padStart(2, '0');
  const mm = String(cfg.minute).padStart(2, '0');
  return `Todo dia às ${hh}:${mm}, mantendo ${cfg.keep} cópia(s)`;
}

function renderConfig(cfg) {
  $('bk-auto').checked = cfg.auto_enabled;
  $('bk-hour').value = cfg.hour;
  $('bk-minute').value = cfg.minute;
  $('bk-keep').value = cfg.keep;
  $('bk-max-mb').value = cfg.max_mb;
  $('bk-auto-label').textContent = descreveAgenda(cfg);
  $('bk-pass-state').textContent = cfg.has_passphrase ? '(salva)' : '(não definida)';
  const ultima = cfg.last_run || {};
  $('bk-last').innerHTML = ultima.at
    ? `${fmtTs(ultima.at)} — <span class="${ultima.status === 'ok' ? 'text-green-400' : 'text-red-400'}">${ultima.status}</span>
       <span class="block text-[11px] text-gray-500 break-all">${ultima.detail || ''}</span>`
    : 'Ainda não rodou.';
}

async function carregarConfig() {
  renderConfig(await api('/api/backup/settings'));
}

async function salvarConfig() {
  const btn = $('bk-cfg-save');
  btn.disabled = true;
  try {
    const body = {
      auto_enabled: $('bk-auto').checked,
      hour: Number($('bk-hour').value),
      minute: Number($('bk-minute').value),
      keep: Number($('bk-keep').value),
      max_mb: Number($('bk-max-mb').value),
    };
    // Campo em branco mantém a passphrase atual; remover exige o botão ao lado
    // — apagar sem querer deixaria o backup automático sem pacote portável e
    // ninguém perceberia até precisar dele.
    const frase = $('bk-cfg-pass').value;
    if (frase) body.export_passphrase = frase;
    renderConfig(await api('/api/backup/settings', { method: 'PUT', body }));
    $('bk-cfg-pass').value = '';
    toast.success('Configuração salva');
  } catch (e) {
    toast.error('Falha ao salvar: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ boot

function fuso() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch (_e) {
    return '';
  }
}

$('bk-export').addEventListener('click', exportar);
$('bk-inspect').addEventListener('click', analisar);
$('bk-import').addEventListener('click', importar);
$('bk-snapshot').addEventListener('click', gerarSnapshot);
$('bk-refresh').addEventListener('click', () => carregarArquivos());
$('bk-cfg-save').addEventListener('click', salvarConfig);
$('bk-pass-clear').addEventListener('click', async () => {
  if (!confirm('Remover a passphrase salva? O backup diário passa a gravar só o banco, sem o pacote de configuração.')) return;
  try {
    renderConfig(await api('/api/backup/settings', { method: 'PUT', body: { export_passphrase: '' } }));
    toast.success('Passphrase removida');
  } catch (e) {
    toast.error('Falha ao remover: ' + e.message);
  }
});
$('bk-pending-cancel').addEventListener('click', cancelarPendente);
$('bk-auto').addEventListener('change', () => {
  $('bk-auto-label').textContent = descreveAgenda({
    auto_enabled: $('bk-auto').checked,
    hour: Number($('bk-hour').value),
    minute: Number($('bk-minute').value),
    keep: Number($('bk-keep').value),
  });
});
$('bk-upload').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (file) enviarParaRestaurar(file);
});

const tz = fuso();
if (tz) $('bk-tz').textContent = `(${tz})`;

injectIcons();
Promise.all([carregarConfig(), carregarArquivos(), carregarPendente()])
  .then(() => injectIcons())
  .catch((e) => toast.error('Falha ao carregar: ' + e.message));
