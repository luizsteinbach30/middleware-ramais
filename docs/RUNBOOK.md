# Runbook — diagnóstico e mitigação

Quando o painel está **degradado** (`/api/system/readyz` → 503) ou um dos
fluxos críticos falha, siga o cenário aplicável abaixo.

## 1. Coleta USCall parou

**Sintoma:** dashboard mostra "Última coleta" antiga; banner amarelo "Serviço
degradado · no_collection_yet".

**Diagnóstico:**
```
journalctl -u middleware-monitor -n 100 | grep -i collect
# ou no Windows: arquivo C:\ProgramData\MiddlewareMonitor\logs\app.log
```

Causas frequentes e mitigação:

| Causa | Sinal nos logs | Mitigação |
|---|---|---|
| Token inválido | `collect_failed reason=auth` | `/config` → "Alterar" `uscall_token` → Salvar |
| Host inacessível | `collect_failed reason=ConnectError` | Validar IP/firewall; `curl -v https://<host>/api/extenstatus?token=...&tipo=all` no servidor |
| TLS expirado | `collect_failed reason=SSL` | Renovar certificado USCall; em emergência, desligar `verify_ssl` (não recomendado) |
| Scheduler parado | `scheduler_not_running` em `/readyz` | `systemctl restart middleware-monitor` |

## 2. Pings sempre offline

**Sintoma:** todos os ramais aparecem com `offline` na coluna Rede.

**Diagnóstico:**
```
ping -c 1 10.20.30.40   # do servidor, para um IP esperado
```

| Causa | Mitigação |
|---|---|
| ICMP bloqueado por firewall | Liberar ICMP egress no servidor |
| Servidor em VLAN errada | Validar rota com `ip route` / `route print` |
| `ping_concurrency` alto demais para a rede | Reduzir em `/config` (default 20) |
| `ping_timeout_ms` muito baixo | Aumentar para 1500–2000 |

## 3. Webhook falhando

**Sintoma:** badges de Dashboard mostram alta razão de falha 24h; tela
**Webhook logs** com `Falha` repetida.

**Diagnóstico:**
- Abra o evento → modal **Ver payload**: confira a URL e o status HTTP.
- Confira o `Authorization` (sem expor token nos logs).
- Reenvie via botão **Reenviar**.

| HTTP | Causa provável | Mitigação |
|---|---|---|
| 0 (ERR) | DNS / TLS / timeout | Validar conectividade do servidor com `curl` |
| 401/403 | Token inválido | Atualizar token em `/config` |
| 4xx | URL ou payload errado | Confirmar contrato com o consumidor |
| 5xx | Falha do consumidor | Apenas reenvie quando ele recuperar |

## 4. Update falhou / rolou rollback

**Sintoma:** linha em `/system/updates` com badge **Rollback** ou **Falha**.

**Diagnóstico:**
- Abra a linha para ver o erro.
- Linux: `middleware-monitor-ctl update-logs` (journal da unidade
  `middleware-monitor-update`) e `/var/lib/middleware-monitor/logs/install.log`.
  Windows: aba "Log de execução" do `.exe`.

| Erro | Mitigação |
|---|---|
| `ChecksumMismatch` | Tarball/exe corrompido — re-tente; problema pode ser na release no GitHub |
| `TarballUnsafe` | Pacote suspeito — abra issue de segurança imediatamente |
| `alembic upgrade` falhou | Examinar erro; restaurar backup do DB se necessário; rollback automático já restaurou symlink |
| `healthcheck_failed` | Nova versão não responde em 60s; rollback automático aplicado; investigar logs da nova versão antes de tentar de novo |
| `update_check_failed` com 401/404 | Token de leitura de releases inválido/expirado — ver §4.1 abaixo |

Restaurar manualmente uma versão anterior (Linux). Desde a 2.12.0 o código
roda do pacote instalado no runtime `/opt/middleware-monitor/python/`, então
trocar o symlink `current` não muda nada — reinstale a versão desejada:
```bash
MM_VERSION=2.11.0 middleware-monitor-ctl update
```
O banco de antes da tentativa fica em
`/var/lib/middleware-monitor/backups/pre-upgrade_<de>_to_<para>_<data>.db`
(migrations não voltam sozinhas; se a versão antiga não abrir o banco novo,
pare o serviço e restaure esse arquivo em `db/app.db`).

### 4.1 Token de leitura de releases (repo privado)

O repositório está **público desde 2026-09**: o updater, o `install.sh` e o
link `releases/latest/download/…` funcionam sem token. Esta seção vale se ele
voltar a ser privado. O pipeline continua embutindo o token fine-grained
somente-leitura (Contents: Read apenas deste repo) via
`scripts/inject_update_token.py` / secret `UPDATE_READ_TOKEN` — e **falha o
build se o secret sumir**; para dispensá-lo, exporte `ALLOW_EMPTY_UPDATE_TOKEN=1`
nos jobs do `release.yml`. Sem token válido num repo privado o updater recebe
404 da API do GitHub e nunca enxerga release nenhuma. No Linux, a linha de
instalação aceita `MM_TOKEN=…` e o persiste como `APP_UPDATE_TOKEN` no env.

> O token embutido fica em base64 no binário — isso **não é segurança**, é só
> redução de exposição acidental. A proteção real é o escopo mínimo do token
> (leitura de conteúdo de um único repositório) e a rotação periódica.

**Rotação (a cada expiração ou suspeita de vazamento):**
1. Gerar novo fine-grained PAT em github.com → Settings → Developer settings
   → Fine-grained tokens: repositório `middleware-ramais` apenas, permissão
   **Contents: Read-only**, validade máxima disponível.
2. Atualizar o secret `UPDATE_READ_TOKEN` no repositório GitHub.
3. A **próxima release** já sai com o token novo embutido.
4. Instalações em campo com token embutido expirado: definir
   `APP_UPDATE_TOKEN=<token novo>` no `.env`/`env.cmd` e reiniciar o serviço
   — a env var tem precedência sobre o token embutido e destrava o
   auto-update sem reinstalar.

**Teste rápido de validade de um token:**
```bash
curl -H "Authorization: Bearer <TOKEN>" \
  https://api.github.com/repos/luizsteinbach30/middleware-ramais/releases?per_page=1
# 200 com JSON = ok · 404 = token sem acesso ao repo (ou expirado)
```

## 5. DB cresceu demais

**Sintoma:** disco ficando cheio em `/var/lib/middleware-monitor/db/`.

**Mitigação:**
1. Diminuir retenções em `/config`:
   - `device_ping_retention_days` (geralmente o maior consumidor)
   - `webhook_log_retention_days`
   - `collection_retention_days`
2. Botão **Limpar agora** dispara o job imediatamente.
3. Após poda, rodar `VACUUM` para reaver espaço:
   ```bash
   sudo -u mmonitor sqlite3 /var/lib/middleware-monitor/db/app.db "VACUUM"
   ```

## 6. Login bloqueado

**Sintoma:** `429 too_many_attempts` na tela de login.

**Mitigação:** aguardar 5 min ou, em emergência, desbloquear pelo DB:
```bash
sudo -u mmonitor sqlite3 /var/lib/middleware-monitor/db/app.db \
  "UPDATE users SET failed_login_count=0, locked_until=NULL WHERE username='admin';"
```

## 7. Senha do admin perdida

```bash
# Linux:
sudo -u mmonitor /opt/middleware-monitor/venv/bin/python -c "
from middleware_monitor.core.db import init_engine, session_factory
from middleware_monitor.core.security import hash_password
init_engine()
with session_factory() as db:
    from middleware_monitor.core.models import User
    u = db.query(User).filter_by(username='admin').one()
    u.password_hash = hash_password('SenhaTemporaria123')
    u.must_change_password = True
    db.commit()
    print('OK — senha resetada para SenhaTemporaria123')
"
```

## 8. Como ler logs

| Plataforma | Comando |
|---|---|
| Linux | `journalctl -u middleware-monitor -f` |
| Windows | `Get-Content C:\ProgramData\MiddlewareMonitor\logs\app.log -Wait -Tail 50` |
| UI | Tela `/logs` (apenas WARN/ERROR persistidos) |

Logs estruturados JSON (em prod) podem ser parseados com `jq`:
```bash
journalctl -u middleware-monitor -o cat | jq 'select(.level=="error")'
```

## 9. Restaurar o banco de um backup

**Quando:** banco corrompido, máquina trocada, ou alguém apagou o que não devia.

**Pelo painel (caminho normal):** `/system/backup` → linha do arquivo →
*restaurar*. A troca acontece no **próximo boot**; reinicie o serviço para
concluir:

```bash
sudo systemctl restart middleware-monitor        # Linux
```
```powershell
nssm restart MiddlewareMonitor                   # Windows (serviço)
```
No modo desktop (`.exe`), feche e abra o aplicativo.

**Sem painel** (app não sobe): coloque o `.db.gz` descomprimido como
`restore.pending.db` ao lado do banco e reinicie — o boot faz a troca e guarda o
banco anterior como `pre-restore-<data>.db` na pasta de backups.

```bash
gunzip -c backups/backup-20260824-023000-auto.db.gz > db/restore.pending.db
```
```powershell
# Windows, sem gzip na mão:
python -c "import gzip,shutil;shutil.copyfileobj(gzip.open(r'backups\backup.db.gz','rb'),open(r'db\restore.pending.db','wb'))"
```

**Desfazer uma restauração:** o banco anterior está em
`backups/pre-restore-<data>.db`. Pare o serviço, copie-o por cima de
`db/app.db` (removendo `app.db-wal`/`app.db-shm`) e suba de novo.

**O que checar depois:** `/system/backup` não mostra mais o aviso amarelo, o
Dashboard traz a contagem esperada de ramais, e `/config` ainda tem o token do
USCall (`token: set`) — se os segredos sumirem, o backup veio de instalação com
outro `APP_SECRET_KEY` e o certo era importar o pacote `.mwrbak`, não o banco.

## 10. Migrar a instalação para outra máquina

1. Na origem, `/system/backup` → **Exportar configuração** com todas as seções
   e uma passphrase forte.
2. Instale o middleware no destino e faça o primeiro login.
3. No destino, **Importar configuração** → analisar → *Substituir* → aplicar.
4. Confira em `/config` os servidores USCall e o broker MQTT (teste de conexão),
   e em `/extension-configurator/environments` os ambientes com as linhas.
5. Só depois desligue o middleware da origem — dois coletores MQTT com o mesmo
   `client_id` na mesma sessão durável brigam pela conexão no broker.

> Para clonar a máquina inteira (com histórico), use o snapshot do banco em vez
> do pacote: mas aí os segredos só abrem se o `APP_SECRET_KEY` for o mesmo, o
> que na prática significa copiar também o `secret.key`/`.env` da origem.
