# Manual de instalação e uso

Guia prático para instalar o **Middleware USCall Monitor** em servidores de cliente. A partir da v2.0, a instalação é feita por um **instalador self-contained** — não há mais comandos manuais de download, descompactação ou criação de usuário.

---

## 1. O que você baixa

A partir do [GitHub Releases](https://github.com/luizsteinbach30/middleware-ramais/releases/latest), pegue **apenas o arquivo correspondente ao SO do servidor**:

| Servidor | Arquivo |
|---|---|
| Windows Server / 10+ (x64) | `MiddlewareMonitorSetup-X.Y.Z.exe` |
| Linux (Debian/Ubuntu/RHEL/Alma — x64) | `middleware-monitor-installer-X.Y.Z.run` |

Tanto o `.exe` quanto o `.run` **incluem tudo dentro** (Python, dependências, NSSM ou systemd, scripts, app) — você **não precisa de internet no servidor** para instalar, só para baixar o arquivo no seu PC.

> Para conferir integridade, baixe também `SHA256SUMS` e verifique:
> - Windows (PowerShell): `Get-FileHash MiddlewareMonitorSetup-X.Y.Z.exe -Algorithm SHA256`
> - Linux: `sha256sum middleware-monitor-installer-X.Y.Z.run`

---

## 2. Instalação no Windows

1. Copie `MiddlewareMonitorSetup-X.Y.Z.exe` para o servidor.
2. Clique com o botão direito → **Executar como administrador**.
3. Avance no wizard. Tudo é instalado em `C:\Program Files\MiddlewareMonitor`, com dados em `C:\ProgramData\MiddlewareMonitor`.
4. Ao final, a janela exibe **"Instalação concluída"** e abre automaticamente o navegador em http://127.0.0.1:8080/.

**Não precisa instalar nada antes** (nem Python, nem NSSM, nem Visual C++). Tudo está dentro do `.exe`.

### Verificar que está rodando

```powershell
Get-Service MiddlewareMonitor
Invoke-RestMethod http://localhost:8080/api/system/healthz
```

A primeira deve mostrar `Status: Running`. A segunda retorna `{ status: "ok" }`.

### Logs

```powershell
Get-Content C:\ProgramData\MiddlewareMonitor\logs\app.log -Wait -Tail 50
```

### Reiniciar / parar / iniciar o serviço

```powershell
Restart-Service MiddlewareMonitor   # ou
Stop-Service MiddlewareMonitor
Start-Service MiddlewareMonitor
```

### Desinstalar

Painel de Controle → **Programas** → **Middleware USCall Monitor** → Desinstalar.
Os dados em `C:\ProgramData\MiddlewareMonitor` são **preservados** (banco, backups, logs). Se quiser apagar tudo, remova essa pasta manualmente.

---

## 3. Instalação no Linux

1. Copie `middleware-monitor-installer-X.Y.Z.run` para o servidor.
2. Execute como root:

   ```bash
   sudo bash middleware-monitor-installer-X.Y.Z.run
   ```

3. O instalador faz todo o resto: cria usuário `mmonitor`, monta `/opt/middleware-monitor`, instala dependências offline, gera chave secreta, roda migrações, registra o serviço systemd e inicia.
4. Ao final, a tela mostra a URL: `http://<ip-do-servidor>:8080/`.

### Pré-requisito

Apenas `python3` no servidor (3.11 ou superior). Se faltar, o instalador tenta instalar via `apt-get` ou `dnf` automaticamente.

### Verificar que está rodando

```bash
sudo systemctl status middleware-monitor
curl -s http://localhost:8080/api/system/healthz
```

### Logs

```bash
sudo journalctl -u middleware-monitor -f
```

### Reiniciar / parar / iniciar

```bash
sudo systemctl restart middleware-monitor
sudo systemctl stop middleware-monitor
sudo systemctl start middleware-monitor
```

### Desinstalar

```bash
sudo systemctl disable --now middleware-monitor
sudo rm -rf /opt/middleware-monitor /etc/systemd/system/middleware-monitor.service
sudo systemctl daemon-reload
# Apagar dados (opcional):
sudo rm -rf /var/lib/middleware-monitor /etc/middleware-monitor
sudo userdel mmonitor
```

---

## 4. Primeiro acesso (igual nos dois SOs)

1. Abra o navegador em `http://<ip-do-servidor>:8080/`.
2. Login:
   - **Usuário:** `admin`
   - **Senha:** `admin`
3. O sistema **vai obrigar você a trocar a senha** antes de qualquer outra coisa.
   - Mínimo 12 caracteres com letras e números.
4. Você cai no Dashboard.

> A senha `admin` só funciona uma vez. Após a troca, a senha antiga é descartada.

---

## 5. Configuração inicial (5 minutos)

Na sidebar, clique em **Configuração** (`/config`). Preencha nesta ordem:

### a. Identificação do cliente
- `client_code`: slug que vai no payload de cada webhook (ex.: `acme-matriz`).

### b. Integração USCall
- `uscall_host`: domínio sem `https://` (ex.: `uscall.empresa.com.br`).
- `uscall_token`: clique em **Alterar**, cole o token, pronto.
- Deixe `verify_ssl` ligado (padrão).
- Clique em **Testar conexão** — deve ficar verde com a latência.

### c. Intervalos de coleta
- Os padrões já funcionam. Só mexa se houver razão específica (mínimo 10s).

### d. Webhooks (extensions / devices / results)
Para cada tipo que você quer enviar:
- Ligue o toggle **Habilitado**.
- Cole a `url` do consumidor.
- Cole o `token` (Bearer) que o consumidor exige.
- Clique em **Testar** — confirma que ele recebe o payload.

Clique em **Salvar configuração** no topo (botão azul). O scheduler aplica os novos intervalos no próximo ciclo.

> **Bug histórico (v2.0.0):** o save mostrava "Falha ao salvar" sem detalhes. A partir da v2.0.1 o toast mostra exatamente qual campo está inválido e por quê.

---

## 6. Auto-update via GitHub — todo dia 00:00

Já vem **ligado por padrão** no canal `stable`. Todo dia à meia-noite (UTC) o serviço consulta o GitHub Releases. Quando há nova versão:

1. Download do tarball (`app-vX.Y.Z.tar.gz`) + `SHA256SUMS`.
2. Verificação SHA256.
3. Migrações Alembic.
4. Restart do serviço.
5. Health-check pós-restart por 60s; se falhar, **rollback automático**.

Para acompanhar / pausar / forçar: tela **`/system/updates`**.

> O **instalador** (.exe / .run) que você usou na instalação **não muda** — as atualizações via GitHub aplicam-se apenas ao código Python da aplicação, não ao Python embutido nem ao NSSM. Para reinstalar o "instalador" inteiro (ex.: ao bumpar o Python ou trocar SO), use a próxima release do instalador.

---

## 7. Backup

Backups manuais são **dois arquivos**:

### Windows
```powershell
Copy-Item C:\ProgramData\MiddlewareMonitor\db\app.db "$HOME\app-$(Get-Date -F yyyy-MM-dd).db"
Copy-Item C:\ProgramData\MiddlewareMonitor\env.cmd "$HOME\env-$(Get-Date -F yyyy-MM-dd).cmd"
```

### Linux
```bash
sudo cp /var/lib/middleware-monitor/db/app.db ~/app-$(date +%F).db
sudo cp /etc/middleware-monitor/env ~/env-$(date +%F)
```

> **Sem o env (ou env.cmd) você perde os tokens cifrados**. O `APP_SECRET_KEY` ali dentro é a chave que descriptografa os tokens do USCall e dos webhooks. Guarde os dois arquivos juntos.

---

## 8. Problemas comuns

| Sintoma | Onde olhar primeiro |
|---|---|
| Painel não abre no `:8080` | Serviço parado? `systemctl status` (Linux) / `Get-Service MiddlewareMonitor` (Windows) |
| `connection refused` | Firewall do servidor bloqueando porta 8080 |
| Login `admin/admin` não funciona | A senha já foi trocada. Veja [RUNBOOK.md §7](RUNBOOK.md) para resetar |
| "Falha ao salvar config" no toast | A partir da v2.0.1 o toast mostra o motivo. Se ainda assim falhar, abra `/logs` |
| Coleta USCall não roda | `/config` → **Testar conexão**; depois `/logs` filtrando módulo `collector` |
| Pings sempre offline | Firewall do servidor bloqueando ICMP egress |
| Webhook falhando | `/webhook-logs` → abrir o evento → ver HTTP status e payload |
| Update falhou | `/system/updates` → linha de histórico mostra o erro |

Para outros cenários, [RUNBOOK.md](RUNBOOK.md) tem 8 casos passo a passo.

---

## 9. Resumo em uma página

```
┌─ INSTALAÇÃO ─────────────────────────────────────────────────┐
│ Windows: clique-duplo em MiddlewareMonitorSetup-X.Y.Z.exe    │
│          (como Administrador). Next-next-finish.              │
│                                                              │
│ Linux:   sudo bash middleware-monitor-installer-X.Y.Z.run    │
└──────────────────────────────────────────────────────────────┘
┌─ PRIMEIRO ACESSO ────────────────────────────────────────────┐
│ http://<ip>:8080/                                            │
│ Login: admin / admin                                         │
│ Trocar senha (12+ caract., letras + números)                 │
└──────────────────────────────────────────────────────────────┘
┌─ CONFIGURAR ─────────────────────────────────────────────────┐
│ /config → client_code, uscall_host, uscall_token             │
│         → habilitar webhooks → Testar → Salvar               │
└──────────────────────────────────────────────────────────────┘
┌─ OPERAR ─────────────────────────────────────────────────────┐
│ Dashboard  /                                                 │
│ Devices    /devices       ·   Coletas    /collections        │
│ Logs       /logs          ·   Webhooks   /webhook-logs       │
│ Updates    /system/updates  (auto, cron 00:00, com rollback) │
└──────────────────────────────────────────────────────────────┘
```
