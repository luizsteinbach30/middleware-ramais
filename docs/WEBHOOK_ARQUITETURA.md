# Webhooks — formatos e arquitetura de recepção

Notas técnicas sobre como reduzir o impacto dos webhooks no servidor que
recebe. Documento de referência, **não é roadmap** do middleware.

## Contrato do payload (v2.7.0 — multi-USCall)

O `data` dos webhooks `extensions` e `devices` continua o **array flat** de
sempre. Desde a v2.7.0 cada item carrega a chave **aditiva**
`"uscall_server"` com o nome do servidor USCall de origem (`null` quando a
origem não é conhecida). Receptores que ignoram chaves desconhecidas — o
comportamento correto — não precisam de nenhuma mudança; com N servidores os
dados chegam **mesclados e unificados** num único POST por ciclo (união dos
ramais; ver ADR-0003).

---

## Por que o receptor trava

Quase sempre o gargalo **não é o formato JSON** (payload típico fica em
20-50 KB), e sim como o receptor processa:

- Handler síncrono: `parse → valida → grava → externaliza → responde`
  tudo dentro da request HTTP.
- Sem ack rápido → o middleware segura recursos esperando resposta.
- Retry/backoff multiplica o número de POSTs em picos.
- Sem backpressure: receptor não tem como pedir trégua.

Trocar JSON por MessagePack acelera o parse uns 30 %, mas não conserta
nenhum dos pontos acima.

---

## Formatos alternativos

| Opção | Tamanho vs JSON | Quando vale |
|---|---|---|
| **JSON + gzip** (`Content-Encoding`) | -80 % | sempre — fruto baixinho |
| **MessagePack** (`application/msgpack`) | -30 a -50 % | payload > 100 KB ou > 100 req/s |
| **Protobuf** | -40 a -60 % | contratos estáveis, alta vazão, schema compartilhado |
| **NDJSON** | igual | streaming / batch sem reagrupar |

Pra o nosso caso (telefonia, dezenas de webhooks/min, payload pequeno) o
ganho real está em **gzip + batching**, não em trocar JSON.

---

## Transportes que escalam mais

- **HTTP/2** — uma conexão TCP/TLS multiplexada, elimina handshake repetido.
- **WebSocket / SSE** — canal persistente, push sem handshake.
- **MQTT** — pub/sub via broker leve (Mosquitto/EMQX). Telco e IoT usam
  muito; aguenta 100 k+ msg/s em hardware modesto.
- **gRPC** — só vale se já for adotar Protobuf.

---

## Arquiteturas de recepção (da mais simples à mais robusta)

### A — Ack rápido + worker interno
```
Webhook → [Receptor: enfileira em memória, responde 202] → [Worker(s)]
```
Zero infra nova. Resposta em < 5 ms. Risco: se o processo cair com fila
cheia, perde mensagens. Bom pra cargas baixas/médias.

### B — Fila persistente *(recomendada)*
```
Webhook → [Receptor: grava em Redis Stream/SQS/Kafka, 202] → [Workers]
```
Receptor faz operação O(1) sub-ms. Workers escalam horizontalmente.
Aguenta picos; sobrevive a reinício. Redis Streams é o sweet-spot pra
começar (1 container, ~5 MB RAM).

### C — Pub/sub direto (sem HTTP)
```
Middleware → [Redis/MQTT/Kafka] → [Consumidor do cliente]
```
Tira HTTP do meio. Menor latência. Exige que o middleware fale o
protocolo da fila — mudança de contrato real.

### D — Pull em vez de push (outbox + polling)
```
Middleware grava eventos em outbox local
Cliente: GET /api/events?since=<cursor> a cada X seg
```
Inverte controle: cliente puxa quando consegue. Nunca trava por excesso
de push. Atrito: endpoint REST com paginação e idempotency.

---

## Padrões que ajudam independente de formato

- **`X-Idempotency-Key`** — um UUID por evento; receptor guarda por 24 h
  e descarta duplicatas. Resolve retry duplo.
- **`Retry-After` em 429/503** — backpressure cooperativo.
- **Batch endpoint** — 1 POST com 200 itens vs. 200 POSTs com 1 item.
  Reduz overhead de handshake em 100 ×.
- **Compressão gzip** — 5 linhas de código dos dois lados.
- **Connection keep-alive + pool warm** — reusar `AsyncClient` entre
  envios.
- **Circuit breaker** — abrir circuito por T segundos quando receptor
  está falhando, evita martelar host saturado.

---

## Recomendação pragmática (ordem de ROI)

1. **gzip no POST** — 10 linhas, -80 % de tráfego.
2. **`Idempotency-Key` por evento** + dedup no receptor.
3. **Receptor responde 202 e processa em worker** — tira processamento
   do hot path HTTP. Resolve 80 % dos travamentos.
4. **Batching** se hoje há 1 POST por ramal.
5. **Redis Streams entre receptor e worker** (arquitetura B) se 1-4 não
   bastar.

Trocar JSON por MessagePack/Protobuf é a otimização de **menor ROI** —
deixa pra depois.

---

## Este documento deixou de ser hipotético (2026-08-31)

Ele nasceu como nota de referência, com o aviso de que **não era roadmap**. Passou a
ser: o **NOC WorkConnect** é o receptor de verdade, e o canal que ele exige é
exatamente o que está descrito acima.

O que sai daqui para a implementação:

- **A arquitetura D (pull com outbox e cursor)** é o canal de comando do NOC. A
  diferença é o sentido: em vez de o cliente puxar eventos do middleware, é o
  **middleware que puxa tarefas** do NOC — mesma inversão de controle, mesma
  propriedade de nunca travar por excesso de push. Vira `GET /agente/v1/fila?aguardar=25`.
- **A arquitetura B (fila persistente)** é como o NOC recebe a telemetria: responde
  202 e processa fora do caminho HTTP.
- **Os itens 1 e 2 da recomendação de ROI** — gzip e `Idempotency-Key` — deixam de ser
  otimização e viram requisito da Fase 1. O `Idempotency-Key`, em particular, conserta
  um problema que já existe: com `RETRY_DELAYS_S = (0, 5, 30)`
  (`domain/webhooks/sender.py:33`), um 200 perdido na volta faz o middleware reenviar,
  e nada no protocolo permite ao receptor perceber.
- **MQTT (transporte C)** fica para a Fase 6, e quando entrar será por extensão do
  `integrations/mqtt_client.py` que já existe — falta só o `publish`.

Ver `AGENTE-NOC.md` neste repositório, e
`C:\Projetos\noc-workconnect\docs\CONTRATO-DO-AGENTE.md`.
