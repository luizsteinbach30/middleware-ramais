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
- Verifique `journalctl -u middleware-monitor-updater` (Linux) ou
  Event Viewer / Stdout do NSSM (Windows).

| Erro | Mitigação |
|---|---|
| `ChecksumMismatch` | Tarball/exe corrompido — re-tente; problema pode ser na release no GitHub |
| `TarballUnsafe` | Pacote suspeito — abra issue de segurança imediatamente |
| `alembic upgrade` falhou | Examinar erro; restaurar backup do DB se necessário; rollback automático já restaurou symlink |
| `healthcheck_failed` | Nova versão não responde em 60s; rollback automático aplicado; investigar logs da nova versão antes de tentar de novo |
| `update_check_failed` com 401/404 | Token de leitura de releases inválido/expirado — ver §4.1 abaixo |

Restaurar manualmente uma versão anterior:
```bash
ln -sfn /opt/middleware-monitor/app/2.0.0 /opt/middleware-monitor/current
systemctl restart middleware-monitor
```

### 4.1 Token de leitura de releases (repo privado)

O repositório de releases é **privado**. Todo build distribuído sai com um
token **fine-grained somente-leitura** (Contents: Read apenas deste repo)
embutido pelo pipeline (`scripts/inject_update_token.py`, secret
`UPDATE_READ_TOKEN`). Sem token válido o updater recebe 404 da API do GitHub
e nunca enxerga release nenhuma.

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
