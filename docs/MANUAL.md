# Manual de instalação e uso

Guia prático para usar o **Middleware USCall Monitor** nos servidores. A partir da v2.1.0 a entrega no Windows é um **único `.exe` standalone** — sem instalador, sem serviço, sem dependências.

---

## 1. O que baixar

A partir do [GitHub Releases](https://github.com/luizsteinbach30/middleware-ramais/releases/latest), pegue o arquivo correspondente ao SO:

| Servidor | Arquivo | Como usar |
|---|---|---|
| **Windows** 10 / Server 2019+ | `MiddlewareMonitor-X.Y.Z.exe` | Clique duplo. Pronto. |
| **Linux** Debian/Ubuntu/RHEL | `middleware-monitor-installer-X.Y.Z.run` | `sudo bash middleware-monitor-installer-X.Y.Z.run` |

Não há instalador no Windows. **Um único `.exe` contém tudo**: Python embutido, servidor web, banco de dados, scheduler, painel — cerca de **30 MB**.

---

## 2. Windows — `MiddlewareMonitor-X.Y.Z.exe`

### 2.1 Iniciar

1. Baixe `MiddlewareMonitor-X.Y.Z.exe` para qualquer pasta (Desktop, Documentos, etc.).
2. **Clique duplo**. Não precisa Administrador.
3. Aparece uma janela com:

- **Banner amarelo no topo** (só quando há atualização): clique em **Atualizar agora** para baixar a versão nova, fechar o app e reabrir já atualizado.
- **Indicador Status** (verde "Rodando" / vermelho "Erro" / amarelo "Iniciando…").
- **URL clicável** que abre o painel web (`http://localhost:8080/`).
- Botões: **Abrir Painel**, **Pasta de Logs**, **Verificar atualização**, **Fechar**.
- **Aba "Log de execução"**: tail em tempo real do que o servidor está fazendo, com cor por nível (INFO branco, WARN amarelo, ERROR vermelho).
- **Aba "Sobre"**: caminho dos dados, repositório, canal e credenciais iniciais.

### 2.2 Parar o servidor

- Clique em **Fechar** na janela, ou simplesmente feche a janela (`X`). O servidor encerra junto.
- Não há serviço Windows. Quando você fecha o app, o servidor para.
- Para rodar sempre: coloque um atalho do `.exe` em `shell:startup` (`Win+R` → digite `shell:startup`). O app abrirá automaticamente no logon.

### 2.3 Onde ficam os dados

Tudo em **`%LOCALAPPDATA%\MiddlewareMonitor\`** (cole isso no Explorer):

```
MiddlewareMonitor\
├── db\app.db          (banco SQLite — backup = copiar este arquivo)
├── backups\           (snapshots manuais)
├── tmp\               (arquivos de update temporários)
├── logs\
│   ├── app.log        (log de execução)
│   └── crash.log      (só existe se o app falhou ao iniciar)
└── secret.key         (chave de criptografia — guarde junto com o backup do .db)
```

Para mover o app de uma máquina pra outra, basta copiar essa pasta + o `.exe`.

### 2.4 Atualização automática (estilo Discord/OBS)

Logo após iniciar, o app verifica o GitHub Releases em segundo plano:

- **Sem atualização**: aparece "Você está na versão mais recente." no canto superior direito.
- **Com atualização**: aparece o **banner amarelo** com "Nova versão disponível: X.Y.Z". O painel continua funcionando normalmente.

Clique em **Atualizar agora**:
1. Baixa o `.exe` novo para `%LOCALAPPDATA%\MiddlewareMonitor\tmp\`.
2. Fecha o app atual.
3. Substitui o `.exe` antigo pelo novo via script `.bat` helper.
4. Reabre a versão nova automaticamente.

Você também pode forçar a checagem com o botão **Verificar atualização**.

---

## 3. Linux — `middleware-monitor-installer-X.Y.Z.run`

No Linux mantemos a instalação como **serviço systemd** (mais natural no Linux).

### 3.1 Instalar

```bash
sudo bash middleware-monitor-installer-X.Y.Z.run
```

Cria usuário `mmonitor`, monta `/opt/middleware-monitor`, registra `middleware-monitor.service` e inicia. URL: `http://<ip>:8080/`.

### 3.2 CLI rápida — `middleware-monitor-ctl`

```bash
middleware-monitor-ctl status
middleware-monitor-ctl start
middleware-monitor-ctl stop
middleware-monitor-ctl restart
middleware-monitor-ctl open       # abre o painel no navegador
middleware-monitor-ctl logs       # acompanha logs em tempo real
```

### 3.3 Dados

```
/var/lib/middleware-monitor/db/app.db        (banco)
/etc/middleware-monitor/env                  (config + APP_SECRET_KEY)
```

### 3.4 Atualização automática (Linux)

O serviço consulta o GitHub a cada dia às 00:00 UTC e baixa o tarball novo. Layout versionado em `/opt/middleware-monitor/app/<versão>/` com rollback automático. Sem ação manual necessária.

---

## 4. Primeiro acesso (igual nos dois SOs)

1. Abra o painel: `http://localhost:8080/` (Windows) ou `http://<ip>:8080/` (Linux).
2. **Usuário:** `admin` · **Senha:** `admin`
3. O sistema **obriga a trocar a senha** antes de qualquer outra coisa.
   - Mínimo 12 caracteres, com letras e números.
4. Você cai no Dashboard.

> A senha `admin` só vale uma vez. Depois da troca, a senha antiga não funciona mais.

---

## 5. Configuração inicial (5 minutos)

Sidebar → **Configuração** (`/config`):

### a. Identificação do cliente
- `client_code`: slug que vai no payload de cada webhook (ex.: `acme-matriz`).

### b. Integração USCall
- `uscall_host`: domínio sem `https://` (ex.: `uscall.empresa.com.br`).
- `uscall_token`: clique em **Alterar**, cole o token.
- `verify_ssl`: deixe ligado.
- Clique em **Testar conexão** — deve aparecer verde com a latência.

### c. Webhooks (extensions / devices / results)
Para cada tipo que você quer enviar:
- Toggle **Habilitado**.
- `url` do consumidor.
- `token` Bearer.
- **Testar** para confirmar.

Clique em **Salvar configuração**. Pronto.

---

## 6. Problemas comuns

| Sintoma | O que verificar |
|---|---|
| `.exe` abre e fecha imediatamente | Abra `%LOCALAPPDATA%\MiddlewareMonitor\logs\crash.log` |
| Status "Erro" na janela | Aba **Log de execução** mostra o motivo. Se não ficar claro, abra issue colando o log |
| `http://localhost:8080/` não abre | Algum firewall/antivírus bloqueando porta 8080? Tente desativar temporariamente para confirmar |
| `admin/admin` não funciona | A senha já foi trocada. Reset via [RUNBOOK.md §7](RUNBOOK.md) |
| Banner de update não some | Se a versão nova for igual ou inferior à instalada, o banner some sozinho no próximo check |
| Falha ao atualizar | Aba **Log de execução** filtrando "updater" mostra o erro exato |

Mais detalhes em [RUNBOOK.md](RUNBOOK.md).

---

## 7. Resumo em uma página

```
┌─ INSTALAR ────────────────────────────────────────────────────┐
│ Windows:  baixe MiddlewareMonitor-X.Y.Z.exe → clique duplo.   │
│           Nenhuma dependência. Não precisa Administrador.     │
│                                                               │
│ Linux:    sudo bash middleware-monitor-installer-X.Y.Z.run    │
└───────────────────────────────────────────────────────────────┘
┌─ ABRIR / FECHAR (Windows) ────────────────────────────────────┐
│ Abrir:    clique duplo no .exe (janela com status + log).     │
│ Fechar:   botão "Fechar" ou X da janela.                      │
│ Reiniciar: feche e abra de novo.                              │
└───────────────────────────────────────────────────────────────┘
┌─ ABRIR / FECHAR (Linux) ──────────────────────────────────────┐
│ middleware-monitor-ctl start | stop | restart | status        │
└───────────────────────────────────────────────────────────────┘
┌─ PRIMEIRO ACESSO ─────────────────────────────────────────────┐
│ http://localhost:8080/                                        │
│ Login: admin / admin                                          │
│ Trocar senha (12+ caracteres com letras e números).           │
└───────────────────────────────────────────────────────────────┘
┌─ ATUALIZAÇÃO ─────────────────────────────────────────────────┐
│ Windows:  banner amarelo "Nova versão disponível" + 1 clique  │
│ Linux:    cron diário 00:00 UTC, sem ação manual              │
└───────────────────────────────────────────────────────────────┘
```
