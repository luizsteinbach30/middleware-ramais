# O middleware como agente do NOC WorkConnect

**Data:** 2026-08-31 · **Versão de referência:** v2.11.0 · **Atualizado:** 2026-09-15 (Fases 0 e 1 feitas, v2.13.0)
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

`src/middleware_monitor/domain/config/schemas.py:54` · **Fase 0** · ✅ **feito (v2.13.0)**

O campo é digitado na tela de configuração. Dois clientes podem digitar o mesmo, e
qualquer um pode digitar o do vizinho. Não identifica ninguém e não se revoga.

**Vira:** tela de enrolamento — o operador cola um **código de uso único** gerado no
NOC, e o middleware o troca por `agente_id` emitido pelo servidor mais um segredo,
guardado como os outros segredos já são. O `client_code` pode sobreviver como rótulo
humano; não como identidade.

**Como ficou (2026-09-15):** tela `/system/noc` (`web/templates/system_noc.html`) →
`api/noc.py` → `domain/noc/cliente.py`. O navegador não fala com o NOC: quem troca o
código pela credencial é o servidor do middleware (a CSP é `connect-src 'self'`). A
credencial `ag_<id>.<segredo>` fica em `app_config` com prefixo `noc.`, cifrada pela
`SecretBox` (`domain/noc/estado.py`) — nunca em `/etc` nem no `.env`. Heartbeat em
`jobs/noc_agent.py`, agendado só depois do enrolamento (sem enrolamento não existe
conexão nenhuma com o NOC). Três decisões que valem dizer:

- **Nenhuma chave `noc.*` viaja no pacote portável** (`LOCAL_ONLY_KEYS` em
  `domain/backup/settings.py`): levar a credencial para outra máquina faria duas
  máquinas se apresentarem como o mesmo agente. O *snapshot* do banco, que restaura
  tudo, ainda leva — restaurar um snapshot antigo depois de um reenrolamento dá
  `credencial_recusada`, e o caminho é enrolar de novo.
- **Revogado para o laço; credencial recusada continua tentando.** Revogar é ato
  deliberado. Recusa pode ser o NOC com banco restaurado — parar a frota inteira seria
  uma visita por site.
- **Log só na mudança de situação** — todo WARNING vira linha em `system_logs`.

O `client_code` não mudou: continua como rótulo.

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

`src/middleware_monitor/integrations/extension_configurator/vendors/base.py:134` ·
**Fase 0** · ✅ **feito (v2.13.0)**

A matéria-prima já existe: `capabilities()` por adapter e
`GET /api/extension-configurator/environments/{id}/capabilities` por ambiente. Falta agregar e enviar ao NOC no
registro e em cada heartbeat: unidades cobertas, servidores USCall e se estão
alcançáveis, ações homologadas, modelos presentes, versão e desvio de relógio.

**O NOC não infere nada** a partir de versão ou modelo. O que não estiver no
manifesto é recusado com `NAO_SUPORTADO` — nunca tentado em melhor esforço.

**Como ficou (2026-09-15):** `domain/noc/manifesto.py`. O heartbeat leva só o sha256
do manifesto; o NOC pede o corpo quando o hash não bate (nada volátil entra no corpo,
senão ele subiria a cada minuto).

- **`acoes` vai vazio, de propósito.** O executor remoto é a Fase 2 (itens 2 e 3).
  Declarar `normalize` agora, porque o adapter sabe normalizar localmente, seria
  prometer ao NOC uma ação que ninguém aqui executa quando ele pedir. As capacidades
  locais vão por modelo, em `acoesDoAdapter`, e `executorRemoto: false` diz o resto.
- **Modelo é o do ambiente** (`ExtensionEnvironment.modelo_telefone`), com a quantidade
  de linhas — o cadastro é a fonte da verdade, não o que o aparelho respondeu.
- **`uscall[].alcancavel` vem da última coleta** (`domain/uscall/saude.py`, em memória):
  `null` depois de um reinício, até a primeira coleta. O teste de conexão da tela baixa
  a lista inteira de ramais e é pesado demais para rodar a cada heartbeat.
- **Unidades não vão no manifesto**: quem diz quais unidades um agente cobre é o NOC,
  na hora de gerar o código.

### 5 — Webhook sem assinatura e sem chave de idempotência

`src/middleware_monitor/domain/webhooks/sender.py:155` · **Fase 1** · ✅ **resolvido por outro caminho (v2.13.0)**

**Como ficou (2026-09-15):** o dono decidiu que **tudo o que os webhooks mandam vai para
o NOC**, e que o módulo de webhooks sai do middleware depois. Então a pendência não foi
resolvida *no* `sender.py`: a telemetria para o NOC é canal próprio
(`domain/noc/telemetria.py` + `jobs/noc_agent.py::run_noc_telemetria`), com
**`Idempotency-Key` por lote** e **mTLS** no lugar do HMAC (ADR 0006 do NOC). Os
webhooks externos continuam como estão até serem desligados — na release que vier
**depois** do NOC em produção, para os receptores atuais não ficarem sem dado.

Hoje vai só `Authorization: Bearer`. Faltam **HMAC do corpo** e **`Idempotency-Key`
por evento**.

**Isto já morde hoje, sem NOC nenhum:** com `RETRY_DELAYS_S = (0, 5, 30)`
(`sender.py:33`), um 200 perdido no caminho de volta faz o middleware reenviar — e
nada no protocolo permite ao receptor perceber que é o mesmo evento.

### 6 — Sem gzip

`src/middleware_monitor/domain/webhooks/sender.py` · **Fase 1** · ✅ **feito no canal do NOC (v2.13.0)**

O lote de telemetria vai em `Content-Encoding: gzip`. O webhook externo não ganhou gzip
de propósito: seria mudança de contrato para um receptor que vai deixar de existir.

O `docs/WEBHOOK_ARQUITETURA.md` deste repositório já marca gzip como o item de maior
retorno: cinco linhas de cada lado, −80 % de tráfego. Está escrito e nunca foi feito.

### 7 — Só três tipos de evento

`src/middleware_monitor/domain/config/repository.py:32` · **Fase 1** · ✅ **feito no canal do NOC (v2.13.0)**

**Como ficou:** o lote leva as transições de telefonia do MQTT local
(`extension_status_events`, por cursor), e o NOC deriva sozinho a queda e a volta de
rede e a mudança de registro comparando retratos consecutivos. A saúde do agente vai no
heartbeat (versão, relógio) e no manifesto (USCall alcançável). Além do que os webhooks
levavam: amostras de ping, relatórios de aplicação e o perfil de cada linha dos
ambientes — **nunca** `senha_sip`, `user_auth` ou chave com nome de segredo.

O que se pedia originalmente:

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

`src/middleware_monitor/integrations/extension_configurator/vendors/base.py:37` ·
**Fase 3**

`DEVICE_ACTIONS` (`base.py:36-38`) contém `set_ip`. Para uso local, com alguém na
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
| **0** ✅ | enrolamento (1) · manifesto publicado (4) · heartbeat — **v2.13.0** |
| **1** ✅ | telemetria por cursor com `Idempotency-Key` e gzip (5, 6, 7) · **mTLS adiantado da Fase 6** — **v2.13.0** |
| **2** | laço de long-poll (2) · executor com idempotência (3) |
| **3** | backup diário que roda (9) · `set_ip` fora do remoto, com teste (10) |
| **6** | `publish` no cliente MQTT (8) · ~~mTLS~~ (feito na Fase 1) |

---

## 🔗 Relacionado

- `WEBHOOK_ARQUITETURA.md` — a *arquitetura D* descrita ali (pull com outbox e
  cursor) é exatamente este canal, e as recomendações de gzip, `Idempotency-Key`,
  ack em 202 e `Retry-After` continuam valendo
- `TELAS.md` — as telas deste app; o enrolamento ficou em página própria, `/system/noc`
- `RUNBOOK.md` · `INSTALACAO.md`
- `C:\Projetos\noc-workconnect\docs\CONTRATO-DO-AGENTE.md` — o outro lado do canal
- `C:\Projetos\noc-workconnect\docs\CATALOGO-DE-TAREFAS.md` — o enum fechado e os raios
