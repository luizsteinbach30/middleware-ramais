# Guia de instalação

## Pré-requisitos

| Plataforma | Requisitos |
|---|---|
| Linux | Debian 11+/Ubuntu 20.04+, root, `python3.11+`, `curl`, `tar` |
| Windows | Windows 10/Server 2019+, admin, `python 3.11+`, [NSSM](https://nssm.cc/download) no PATH |

Em ambos: a porta `8080` (default) deve estar livre e o servidor precisa de
saída HTTPS para o USCall e para `api.github.com`.

## Linux

### Instalação automática (recomendada)

Como `root`:

```bash
curl -fsSL https://github.com/<org>/middleware-monitor/releases/latest/download/install.sh -o install.sh
chmod +x install.sh
./install.sh                 # versão mais recente do canal stable
./install.sh v2.0.0          # pinar uma versão
```

O script:
1. Cria usuários de sistema `mmonitor` (serviço) e `mmupdater` (watcher).
2. Cria `/opt/middleware-monitor/{app,venv}` e `/var/lib/middleware-monitor/{db,backups,tmp}`.
3. Baixa o tarball + `SHA256SUMS`, valida o hash, extrai em `app/<X.Y.Z>/`.
4. Monta venv e instala `requirements.lock`.
5. Cria `current` apontando para a versão recém-instalada.
6. Gera `/etc/middleware-monitor/env` com `APP_SECRET_KEY` aleatório.
7. Roda `alembic upgrade head`.
8. Cria o admin e imprime a senha temporária na tela.
9. Habilita `middleware-monitor.service` e o timer `middleware-monitor-updater.timer`.

Validação:
```bash
systemctl status middleware-monitor
curl -s http://localhost:8080/api/system/healthz
```

Acesse `http://<servidor>:8080/`, faça login com a senha impressa, troque-a
quando solicitado.

### Desinstalação

```bash
./packaging/linux/uninstall.sh           # mantém /var/lib (DB)
./packaging/linux/uninstall.sh --purge   # remove tudo
```

## Windows

### Instalação automática

Em PowerShell **administrador**:

```powershell
# Garantir NSSM no PATH (https://nssm.cc/download)
iwr -useb https://github.com/<org>/middleware-monitor/releases/latest/download/install.ps1 | iex
```

O script:
1. Cria `C:\Program Files\MiddlewareMonitor\{app,venv,current}` e
   `C:\ProgramData\MiddlewareMonitor\{db,backups,tmp,logs}`.
2. Baixa, verifica e extrai a release.
3. Cria venv e instala dependências.
4. Cria `current` como junction (`mklink /J`) para a versão.
5. Gera `env.cmd` com `APP_SECRET_KEY` aleatório.
6. Roda `alembic upgrade head` e bootstrapa o admin.
7. Instala o serviço **MiddlewareMonitor** via NSSM com auto-start e
   rotação de logs (10MB).

Validação:
```powershell
nssm status MiddlewareMonitor
Invoke-RestMethod http://localhost:8080/api/system/healthz
```

### Desinstalação

```powershell
.\packaging\windows\uninstall.ps1            # mantém ProgramData
.\packaging\windows\uninstall.ps1 -Purge     # remove tudo
```

## Configuração inicial

Após o primeiro login (forçando troca de senha), em `/config`:

1. Preencha `client_code` (slug que vai no payload dos webhooks).
2. Em **Integração USCall**, configure `uscall_host` (sem `https://`) e o
   `uscall_token`. Clique em **Testar conexão**.
3. Ajuste **intervalos de coleta** se necessário (mínimo 10s).
4. Em **Webhooks**, ative os tipos desejados e cadastre `url` + `token`.
   Use **Testar** para validar.
5. Salve. O scheduler é re-aplicado automaticamente.

## Auto-update

- Habilitado por padrão, canal `stable`.
- Para alterar canal: `/system/updates` → seletor `Canal` → `beta`.
- Para desligar: `/system/updates` → toggle **Auto-update**.
- Para forçar verificação: botão **Verificar agora**.
- Para aplicar uma versão disponível: botão **Atualizar agora** (requer admin).

## Backup

```bash
# Linux (com app rodando — graças ao WAL):
sudo -u mmonitor /opt/middleware-monitor/venv/bin/python -c \
  "import sqlite3; sqlite3.connect('/var/lib/middleware-monitor/db/app.db').execute('VACUUM INTO ?', ['/var/lib/middleware-monitor/backups/app-$(date +%Y%m%d).db'])"

# Windows: copiar C:\ProgramData\MiddlewareMonitor\db\app.db (o WAL torna safe)
```

A chave `APP_SECRET_KEY` é tão crítica quanto o backup do DB — sem ela os
tokens cifrados não voltam. Guarde o `env`/`env.cmd` no mesmo cofre do backup.

## Troubleshooting rápido

Ver [RUNBOOK.md](RUNBOOK.md) para diagnóstico passo-a-passo.
