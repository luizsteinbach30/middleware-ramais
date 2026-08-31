# O middleware como agente do NOC WorkConnect

**Data:** 2026-08-31 · **Versão de referência:** v2.11.0
**Documento irmão:** `C:\Projetos\noc-workconnect\docs\CONTRATO-DO-AGENTE.md`

Este documento descreve o que **este** repositório precisa ganhar para participar do
programa NOC WorkConnect. Ele é a fonte da verdade sobre o lado do agente — o
repositório do NOC **aponta** para cá e não duplica nada.

---

## O que muda

Hoje o middleware faz uma coisa em direção à internet: **empurra** webhooks
(`extensions`, `devices`, `results`) para um endereço configurado. Não existe nenhum
caminho pelo qual alguém de fora peça qualquer coisa a ele.

Ele passa a ser um **agente ativo**, no modelo do Zabbix: além de empurrar
telemetria, ele **busca trabalho** numa fila do NOC, executa e devolve o resultado.

**O que não muda, em nenhuma fase:**

- **Nada entra.** A interface web continua ouvindo só na LAN. Nenhum *port forward*,
  nenhuma porta nova, nenhuma VPN permanente. Toda conexão continua saindo daqui.
- **As credenciais dos aparelhos não saem.** Continuam cifradas (Fernet) no SQLite
  local. A tarefa que chega do NOC nomeia o alvo; quem sabe a senha é este processo.
- **O middleware continua funcionando sozinho.** NOC fora do ar não pode parar
  coleta, ping, nem o Configurador de Ramais. O agente é uma capacidade a mais, não
  uma dependência.

---

## As dez pendências

Cada uma diz **qual módulo existente se estende**. Não há módulo novo onde já existe
um — o valor do middleware está justamente no que ele já sabe fazer.

### 1 — `client_code` é texto livre digitado

`src/middleware_monitor/domain/config/schemas.py:54` · **Fase 0**

O campo é digitado na tela de configuração. Dois clientes podem digitar o mesmo, e
qualquer um pode digitar o do vizinho. Não identifica ninguém e não se revoga.

**Vira:** tela de enrolamento — o operador cola um **código de uso único** gerado no
NOC, e o middleware o troca por `agente_id` emitido pelo servidor mais um segredo,
guardado como os outros segredos já são. O `client_code` pode sobreviver como rótulo
humano; não como identidade.

### 2 — Não existe canal de entrada

**Novo `src/middleware_monitor/jobs/noc_agent.py`, ao lado dos jobs que já existem** ·
**Fase 2**

Hoje só há push (`domain/webhooks/sender.py`). Falta o laço de long-poll:

```
GET /agente/v1/fila?aguardar=25   ->  executa  ->  POST .../resultado
```

Pontos que o job precisa acertar, e que são fáceis de errar:
- **backoff com jitter.** Sem jitter, uma queda do NOC devolve a frota inteira no
  mesmo milissegundo.
- **reconexão imediata** após cada ciclo, incluindo o 204 de "nada a fazer".
- **um só laço**, no scheduler que já existe — não uma segunda thread paralela.

### 3 — Não existe executor de tarefa remota nem registro de idempotência

`src/middleware_monitor/domain/extension_configurator/actions.py` · **Fase 2**

As ações **já existem e estão homologadas**; só a tela as dispara. Falta a camada que
recebe uma tarefa do NOC, valida contra o manifesto, chama a ação existente e devolve
o resultado.

**Idempotência é requisito, não melhoria:** cada tarefa tem UUID; o agente guarda o
que executou e **tarefa repetida devolve o resultado gravado sem reexecutar**.
`send_config` reinicia o aparelho — uma reentrega por retry de rede derrubaria o
telefone duas vezes.

### 4 — O manifesto de capacidades não é publicado para fora

`src/middleware_monitor/integrations/extension_configurator/vendors/base.py:101` ·
**Fase 0**

A matéria-prima já existe: `capabilities()` por adapter e
`GET /environments/{id}/capabilities` por ambiente. Falta agregar e enviar ao NOC no
registro e em cada heartbeat: unidades cobertas, servidores USCall e se estão
alcançáveis, ações homologadas, modelos presentes, versão e desvio de relógio.

**O NOC não infere nada** a partir de versão ou modelo. O que não estiver no
manifesto é recusado com `NAO_SUPORTADO` — nunca tentado em melhor esforço.

### 5 — Webhook sem assinatura e sem chave de idempotência

`src/middleware_monitor/domain/webhooks/sender.py:155` · **Fase 1**

Hoje vai só `Authorization: Bearer`. Faltam **HMAC do corpo** e **`Idempotency-Key`
por evento**.

**Isto já morde hoje, sem NOC nenhum:** com `RETRY_DELAYS_S = (0, 5, 30)`
(`sender.py:33`), um 200 perdido no caminho de volta faz o middleware reenviar — e
nada no protocolo permite ao receptor perceber que é o mesmo evento.

### 6 — Sem gzip

`src/middleware_monitor/domain/webhooks/sender.py` · **Fase 1**

O `docs/WEBHOOK_ARQUITETURA.md` deste repositório já marca gzip como o item de maior
retorno: cinco linhas de cada lado, −80 % de tráfego. Está escrito e nunca foi feito.

### 7 — Só três tipos de evento

`src/middleware_monitor/domain/config/repository.py:32` · **Fase 1**

`WEBHOOK_TYPES` tem `extensions`, `devices`, `results`. Faltam dois:

- **mudança de estado** — ramal caiu, aparelho ficou offline. Hoje o NOC teria de
  inferir isso comparando snapshots, o que atrasa a detecção em um ciclo inteiro.
- **saúde do agente** — heartbeat com versão, desvio de relógio e alcance dos
  servidores USCall.

### 8 — Cliente MQTT é só consumidor

`src/middleware_monitor/integrations/mqtt_client.py` · **Fase 6**

O cliente só faz `subscribe`. Se o MQTT virar transporte alternativo do canal (ADR
0001 do NOC), falta o `publish`.

**A parte difícil já está pronta e homologada:** pinning de certificado,
`clean_session=False` com `client_id` estável e QoS 1 — que é o que faz o broker
guardar mensagem enquanto o serviço está parado. **Estende-se este cliente**, não se
escreve outro.

### 9 — O backup automático diário nunca roda de fato

`src/middleware_monitor/jobs/backup.py` · **Fase 3, e é pré-requisito**

Ele só dispara com o app aberto às 02:30. Numa instalação desktop que fica fechada, o
backup simplesmente não acontece.

Isso deixa de ser inconveniência e vira bloqueio: **escrita remota exige backup
recente**. Correção mínima: rodar o backup atrasado no boot.

### 10 — `ACTION_SET_IP` precisa sair do caminho remoto

`src/middleware_monitor/integrations/extension_configurator/vendors/base.py:15` ·
**Fase 3**

`DEVICE_ACTIONS` (`base.py:14-16`) contém `set_ip`. Para uso local, com alguém na
frente do aparelho, tudo bem. **Pelo canal remoto, não.**

Errar a rede de um telefone a 800 km de distância é perder o aparelho até alguém ir
lá. A regra vale para qualquer P-code ou campo equivalente de IP, máscara, gateway,
DNS, VLAN, porta HTTP ou VPN — em todos os adapters.

**Como isso vira código:**
1. o executor remoto tem uma **lista de ações permitidas** que não inclui `set_ip`;
2. a whitelist de campos por fabricante já existente é reafirmada no caminho remoto;
3. **um teste que falha** se um código de rede entrar no caminho remoto. Sem o teste,
   a regra é um comentário.

---

## Resumo por fase

| Fase | O que este repositório entrega |
|---|---|
| **0** | enrolamento (1) · manifesto publicado (4) · heartbeat |
| **1** | HMAC e `Idempotency-Key` (5) · gzip (6) · dois eventos novos (7) |
| **2** | laço de long-poll (2) · executor com idempotência (3) |
| **3** | backup diário que roda (9) · `set_ip` fora do remoto, com teste (10) |
| **6** | `publish` no cliente MQTT (8) · mTLS |

---

## 🔗 Relacionado

- `WEBHOOK_ARQUITETURA.md` — a *arquitetura D* descrita ali (pull com outbox e
  cursor) é exatamente este canal, e as recomendações de gzip, `Idempotency-Key`,
  ack em 202 e `Retry-After` continuam valendo
- `TELAS.md` — as telas deste app; o enrolamento entra em Configuração
- `RUNBOOK.md` · `INSTALACAO.md`
- `C:\Projetos\noc-workconnect\docs\CONTRATO-DO-AGENTE.md` — o outro lado do canal
- `C:\Projetos\noc-workconnect\docs\CATALOGO-DE-TAREFAS.md` — o enum fechado e os raios
