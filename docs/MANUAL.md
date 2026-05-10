# Manual prático de instalação e uso

Para administradores de TI que vão instalar o **Middleware USCall Monitor** em servidores de cliente. O caminho funciona igual em **Windows Server** ou **Linux**; cada passo está duplicado.

---

## 1. O que é preciso ter no servidor

| Item | Mínimo |
|---|---|
| SO | Windows 10 / Server 2019+  **ou**  Debian 11+ / Ubuntu 20.04+ |
| Python | 3.11 ou superior |
| Acesso de admin | sim (root no Linux, "Executar como administrador" no Windows) |
| Porta | **8080** livre na LAN |
| Saída internet | HTTPS para o seu host USCall e para `api.github.com` |

No Windows: instale também o [**NSSM**](https://nssm.cc/download) e adicione ao `PATH`.

---

## 2. Instalação — escolha sua plataforma

### 🐧 Linux

Em **um único terminal como root**:

```bash
curl -fsSL https://github.com/<sua-org>/middleware-monitor/releases/latest/download/install.sh -o install.sh
chmod +x install.sh
sudo ./install.sh
```

O instalador faz tudo:
- baixa a última versão
- valida hash
- cria usuários `mmonitor` e `mmupdater`
- monta `/opt/middleware-monitor` e `/var/lib/middleware-monitor`
- gera `APP_SECRET_KEY` aleatório
- cria o admin e **mostra a senha temporária na tela**
- habilita o serviço `middleware-monitor` e o timer do auto-update

**Anote a senha temporária impressa.**

### 🪟 Windows

Em **PowerShell rodando como Administrador**:

```powershell
iwr -useb https://github.com/<sua-org>/middleware-monitor/releases/latest/download/install.ps1 | iex
```

O instalador faz o mesmo: baixa, valida, configura, cria admin, registra
serviço **MiddlewareMonitor** via NSSM.

**Anote a senha temporária impressa.**

---

## 3. Primeiro acesso (igual nos dois SOs)

1. Abra o navegador em:  `http://<ip-ou-hostname-do-servidor>:8080/`
2. Login: `admin` / senha temporária impressa pelo instalador.
3. O sistema **obriga a troca de senha** no primeiro login (mínimo 12 caracteres com letras e números).
4. Você cai no Dashboard.

---

## 4. Configuração inicial (5 minutos)

Vá em **`/config`** (link na sidebar) e preencha, nesta ordem:

### a. Identificação do cliente
- `client_code`: slug que vai no payload de cada webhook (ex.: `acme-matriz`).

### b. Integração USCall
- `uscall_host`: ex. `uscall.empresa.com.br` (sem `https://`).
- `uscall_token`: clique em **Alterar** e cole o token.
- Deixe `verify_ssl` ligado (padrão).
- Clique em **Testar conexão** — deve retornar verde com latência.

### c. Intervalos de coleta
- Padrões funcionam. Só ajuste se precisar (mínimo 10s).

### d. Webhooks (extensions / devices / results)
Para cada um que você quer enviar:
- Ligue o toggle **Habilitado**.
- Cole a `url` do consumidor.
- Cole o `token` Bearer.
- Clique em **Testar** — confirma que o consumidor recebe.

Clique em **Salvar configuração** no topo. Pronto: a coleta começa no próximo ciclo.

---

## 5. Operação no dia a dia

### Verificar se está rodando

🐧 Linux:
```bash
systemctl status middleware-monitor
curl -s http://localhost:8080/api/system/healthz
```

🪟 Windows (PowerShell):
```powershell
nssm status MiddlewareMonitor
Invoke-RestMethod http://localhost:8080/api/system/healthz
```

### Reiniciar o serviço

🐧 `sudo systemctl restart middleware-monitor`
🪟 `nssm restart MiddlewareMonitor`

### Parar / iniciar

🐧 `sudo systemctl stop middleware-monitor` / `sudo systemctl start middleware-monitor`
🪟 `nssm stop MiddlewareMonitor` / `nssm start MiddlewareMonitor`

### Ler logs

🐧 `sudo journalctl -u middleware-monitor -f`
🪟 `Get-Content C:\ProgramData\MiddlewareMonitor\logs\app.log -Wait -Tail 50`

A UI também tem `/logs` (apenas WARN/ERROR, com filtros).

---

## 6. Auto-update via GitHub

Já vem **ligado por padrão**. O servidor consulta o GitHub a cada 60 min e instala novas versões do canal `stable` automaticamente.

Para acompanhar: tela **`/system/updates`**:
- Versão atual + versão disponível.
- Botão **Verificar agora**.
- Botão **Atualizar agora** (manual).
- Trocar canal `stable` ↔ `beta`.
- Histórico de updates (com status: Sucesso / Rollback / Falha).

Em caso de falha na nova versão, o sistema faz **rollback automático** para a versão anterior — você não precisa intervir.

---

## 7. Backup do servidor

🐧 Linux — copiar 1 arquivo:
```bash
sudo cp /var/lib/middleware-monitor/db/app.db ~/backup-$(date +%F).db
sudo cp /etc/middleware-monitor/env ~/env-$(date +%F)        # contém APP_SECRET_KEY
```

🪟 Windows — copiar 2 arquivos:
```powershell
Copy-Item C:\ProgramData\MiddlewareMonitor\db\app.db "$HOME\backup-$(Get-Date -F yyyy-MM-dd).db"
Copy-Item C:\ProgramData\MiddlewareMonitor\env.cmd "$HOME\env-$(Get-Date -F yyyy-MM-dd).cmd"
```

> **Atenção:** sem o `APP_SECRET_KEY` (no `env`/`env.cmd`) os tokens cifrados no DB **não voltam**. Guarde os dois juntos.

---

## 8. Problemas comuns

| Sintoma | Onde olhar primeiro |
|---|---|
| Painel não abre | Serviço parado → `systemctl status` / `nssm status` |
| Banner amarelo "Serviço degradado" | `/logs` ou `/api/system/readyz` |
| Coleta USCall não roda | `/config` → **Testar conexão**; depois `/logs` filtrando módulo `collector` |
| Pings sempre offline | Firewall do servidor está bloqueando ICMP egress |
| Webhook falhando | `/webhook-logs` → abrir o evento e ver HTTP/erro |
| Update falhou | `/system/updates` → linha de histórico mostra o erro |
| Senha do admin perdida | Ver [RUNBOOK.md §7](RUNBOOK.md) |
| Login bloqueado (`429`) | Esperar 5 min ou ver [RUNBOOK.md §6](RUNBOOK.md) |

Para qualquer outro caso, [RUNBOOK.md](RUNBOOK.md) tem 8 cenários documentados passo a passo.

---

## 9. Desinstalar

🐧 Linux:
```bash
sudo /opt/middleware-monitor/current/packaging/linux/uninstall.sh           # mantém o DB em /var/lib
sudo /opt/middleware-monitor/current/packaging/linux/uninstall.sh --purge   # apaga tudo
```

🪟 Windows (PowerShell admin):
```powershell
& "C:\Program Files\MiddlewareMonitor\current\packaging\windows\uninstall.ps1"           # mantém ProgramData
& "C:\Program Files\MiddlewareMonitor\current\packaging\windows\uninstall.ps1" -Purge    # apaga tudo
```

---

## 10. Resumo em uma página

```
┌─ INSTALAÇÃO ─────────────────────────────────────────────────┐
│  Linux:    sudo bash install.sh                              │
│  Windows:  iwr install.ps1 | iex   (PowerShell admin)        │
│  → Copia a senha temporária impressa                         │
└──────────────────────────────────────────────────────────────┘
┌─ PRIMEIRO ACESSO ────────────────────────────────────────────┐
│  http://<servidor>:8080/                                     │
│  Login: admin / <senha temporária>                           │
│  Trocar senha (12+ caract, letras+números)                   │
└──────────────────────────────────────────────────────────────┘
┌─ CONFIGURAR ─────────────────────────────────────────────────┐
│  /config → client_code, uscall_host, uscall_token            │
│           → habilitar webhooks → Testar → Salvar              │
└──────────────────────────────────────────────────────────────┘
┌─ OPERAR ─────────────────────────────────────────────────────┐
│  Dashboard /                                                 │
│  Devices /devices  ·  Coletas /collections                   │
│  Webhook logs /webhook-logs  ·  Logs /logs                   │
│  Atualizações /system/updates  (auto, com rollback)          │
└──────────────────────────────────────────────────────────────┘
```
