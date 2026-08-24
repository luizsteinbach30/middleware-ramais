# Medição-base de desempenho — 2026-08-24

Primeira etapa da §15.6 do `REQUISITOS.md`: **medir antes de mexer**. Este
documento é o retrato de onde o sistema está hoje, com número, para que qualquer
mudança de arquitetura depois possa ser comparada contra ele — e não contra
impressão.

Regenerar:

```bash
python scripts/perf_baseline.py --out reports/perf.md --json reports/perf.json
```

A ferramenta é versionada de propósito: o valor de uma medição-base é poder
rodar a mesma medição de novo. Ela roda sempre sobre uma **cópia** do banco, com
brokers e servidores desabilitados na cópia — sem isso o processo de medição
conecta no EMQX do cliente com o mesmo `client_id` da produção e briga com a
sessão durável.

## Onde a medição foi feita

| | |
|---|---|
| Banco | instalação Workconnect, `data/db/app.db` — 58 MB + 4 MB de WAL |
| Escala | **1.930 devices**, **910 ramais** publicando no ledger, 43.495 mensagens (4,9 dias) |
| Intervalo de coleta/ping | `webhook_interval_minutes = 180` (3 h) |
| Retenções vigentes | ledger 7 d · transições 7 d · chamadas 90 d · resumo diário 365 d · ping 30 d |
| Telas | ASGI em processo, sem uvicorn, sem rede, sem scheduler e sem coletor; 7 repetições |

⚠️ **Este banco está na escala real do cliente** (910 ramais publicando), então
os números de tela e de job valem como retrato de produção. O que ele **não**
tem é concorrência: a medição roda com a ingestão parada. O número de tela é o
**piso** — o custo de servidor puro.

---

## 1. Banco

Arquivo de 55,4 MB depois do checkpoint · página de 4 KB · 14.188 páginas,
**6.257 delas livres**.

| Tabela | Linhas | Dados | Índices | Total | % |
|---|---:|---:|---:|---:|---:|
| `mqtt_messages` | 43.495 | 15,6 MB | 6,3 MB | **21,8 MB** | 73,5% |
| `extension_status_events` | 21.175 | 2,3 MB | 1,8 MB | 4,0 MB | 13,5% |
| `extension_calls` | 6.624 | 1,2 MB | 804 KB | 2,0 MB | 6,7% |
| `collections` | 3 | 676 KB | 8 KB | 684 KB | 2,2% |
| `device_pings` | 5.920 | 236 KB | 220 KB | 456 KB | 1,5% |
| `devices` | 1.930 | 392 KB | 52 KB | 444 KB | 1,5% |
| `extension_daily_stats` | 606 | 44 KB | 48 KB | 92 KB | 0,3% |
| todas as outras 18 juntas | — | — | — | 212 KB | 0,7% |

Três leituras:

**44% do arquivo é espaço livre que nunca volta.** 24,4 MB das 55,4 MB são
página de freelist — rastro de poda antiga. Um `VACUUM` devolve o arquivo a
**29,7 MB e leva 240 ms**. A §15.6 suspeitava disso; agora tem número, e o
número diz que o preço de resolver é baixo.

**O ledger é o banco.** `mqtt_messages` sozinha é 73,5% dos dados, e 6,3 MB
disso são índice. Em regime, com a retenção de 7 dias e o volume atual (43.495
mensagens em 4,9 dias), a projeção é **~62 mil mensagens e ~31 MB** só de
ledger. Toda consulta de tela divide o arquivo com a escrita do coletor.

**`collections` guarda 3 linhas e ocupa 676 KB.** Cada snapshot de coleta é um
JSON de ~331 KB numa única linha. Hoje são 3 porque a retenção poda em 90 dias;
não é problema, mas é a linha mais cara do banco por unidade.

## 2. Jobs

| Job | Duração | O que significa |
|---|---:|---|
| `rebuild_calls` (incremental) | **11–31 ms** | é como ele roda em produção, a cada 60 s |
| `rebuild_calls` (completo) | **717 ms** | reprocessar as 21.175 transições da retenção — pior caso, que produção não faz |
| `daily_stats` | **15 ms** | recalcula ontem e hoje, de hora em hora |
| `backup snapshot` | **528 ms** | `VACUUM INTO` + gzip do banco inteiro |
| `retention_daily` | **18 ms** | com pouco a apagar (87 pings); ver ressalva abaixo |
| `collect_extensions` | **não medido** | fala com a API USCall |
| `monitor_devices` | **não medido** | dispara ping em ~1.930 IPs do cliente |

**A reconstrução de chamadas não é gargalo.** O roadmap registrava 426 ms para
11 mil transições; agora são 717 ms para 21 mil — escala linear e continua
barata. E o caminho que roda de verdade a cada minuto custa dezenas de
milissegundos, não centenas.

**A retenção parece barata porque não tinha o que apagar.** 18 ms com 87 linhas
podadas não diz nada sobre o dia em que a retenção de 7 dias começar a cortar
dezenas de milhares de mensagens. O que já dá para afirmar é o efeito colateral:
ela apaga e **não** devolve o espaço (ver freelist acima).

⚠️ **Os dois jobs que a §15.6 mais suspeitava são exatamente os que ninguém
mede.** `collect_extensions` e `monitor_devices` calculam `duration_ms` e
mandam para o log — que só vai para o stdout do processo. A tabela `system_logs`
guarda apenas WARNING e ERROR, então **a duração de um job bem-sucedido não é
gravada em lugar nenhum**. Em campo, com o `.exe`, esse stdout não é lido por
ninguém. E `core/metrics.py` define `mm_collect_extensions_duration_seconds`,
`mm_ping_latency_ms` e outros seis instrumentos que **nenhuma linha do código
alimenta** — o endpoint `/api/system/metrics` renderiza um registro vazio.

Sem fechar esse buraco, o entregável da §15.6 ("duração de cada job") não tem
como ser cumprido para os dois jobs mais pesados — nem aqui, nem no cliente.

## 3. Telas

Tempo de servidor em processo, somando o documento HTML e todas as chamadas que
a tela dispara ao carregar (a lista sai dos módulos em `web/static/js/pages`).

| Tela | Total p50 | Payload | Requisição mais cara |
|---|---:|---:|---|
| Mensagens (ledger, 15 min) | 33 ms | 59 KB | `/api/mqtt/status` — 16 ms |
| Ramais (lista) | 24 ms | 47 KB | `/devices` (HTML) — 11 ms |
| Configurações | 23 ms | 44 KB | `/config` (HTML) — 11 ms |
| Detalhe do ramal | 22 ms | 17 KB | `/devices/1` (HTML) — 10 ms |
| Dashboard | 20 ms | 13 KB | `/` (HTML) — 11 ms |
| Ramais (filtro de faixa) | 19 ms | 24 KB | `/api/devices?…ip_from=…` — 19 ms |
| Backup | 19 ms | 24 KB | `/system/backup` (HTML) — 11 ms |
| Configurador (lista) | 17 ms | 26 KB | HTML — 12 ms |
| Configurador (ambiente) | 17 ms | 35 KB | HTML — 12 ms |
| Updates | 16 ms | 17 KB | HTML — 11 ms |
| Chamadas (24 h) | 15 ms | 38 KB | HTML — 11 ms |
| Logs | 14 ms | 29 KB | HTML — 10 ms |
| Painel ao vivo | 14 ms | 18 KB | HTML — 10 ms |
| Webhooks | 13 ms | 15 KB | HTML — 10 ms |
| Coletas | 13 ms | 13 KB | HTML — 10 ms |
| Configurador (execuções) | 13 ms | 11 KB | HTML — 10 ms |
| Mensagens (ledger, 7 dias) | 8 ms | 42 KB | `/api/mqtt/messages` — 5 ms |
| Chamadas (7 dias) | 4 ms | 24 KB | `/api/mqtt/calls` — 4 ms |

⚠️ **Duas ressalvas antes de qualquer conclusão.** O **Painel ao vivo** está
medido **por baixo**: o estado dos ramais vem da memória do coletor, que na
medição não está rodando — o número aqui é só o índice ramal→device (que tem
cache de 30 s, §15.4) e a casca. E as janelas de tempo são ancoradas no dado
mais novo do banco, não no relógio: sem isso `last=15m` varreria uma janela
vazia e a tela de ledger "responderia" em 3 ms medindo nada.

### O que os números dizem

**Nenhuma tela é lenta.** A pior soma 33 ms de servidor. A hipótese da §15.6 de
que "telas carregam tudo" **não se confirma** nesta escala: `/api/devices`
pagina no banco (24 KB, 4 ms para 1.930 devices) e a janela larga do ledger é
mais *barata* que a estreita, porque é `LIMIT 100` ordenado por índice. A única
tela que carrega tudo é a de **filtro de faixa** de IP/ramal, que por desenho
pagina em memória (`devices/repository.py`) — e mesmo assim custa 19 ms.

**O HTML custa mais que a API em quase toda tela**, e a causa não é o template.
`web/pages.py:get_templates()` monta um `Jinja2Templates` **novo a cada
request** — ambiente novo, cache de compilação novo, `ChoiceLoader` do cache de
bundle remontado. Toda tela recompila o template do zero, toda vez. Medido,
memorizando a mesma função:

| Tela | Hoje | Com o `Jinja2Templates` memorizado | Ganho |
|---|---:|---:|---:|
| `/devices` | 9,8 ms | 2,2 ms | **−77%** |
| `/` | 10,6 ms | 2,0 ms | **−81%** |
| `/config` | 11,7 ms | 2,2 ms | **−82%** |
| `/mqtt-painel` | 10,6 ms | 2,0 ms | **−81%** |

**A requisição mais cara do sistema é `/api/mqtt/status`, e por um motivo só.**
Dos 16 ms, **10,8 ms** são `SUM(payload_bytes)` sobre `mqtt_messages` inteira —
`EXPLAIN QUERY PLAN` confirma `SCAN mqtt_messages`, sem índice, lendo as 15,6 MB
da tabela. O `COUNT(*)` ao lado dela custa 0 ms, porque cai num índice de
cobertura. O número cresce junto com o ledger: em regime (projeção de ~62 mil
mensagens) vira ~15 ms, e a tela de Mensagens chama isso a cada carga.

---

## 4. Veredito, suspeita por suspeita

A §15.6 listou cinco pontos "já suspeitos". Com número:

| Suspeita da §15.6 | Veredito |
|---|---|
| Banco único com o ledger dentro | **confirmada** — ledger é 73,5% dos dados; projeção de 31 MB em regime |
| Coleta e ping no mesmo intervalo; ping é o job mais pesado | **não medida** — e não *mede*: duração de job bem-sucedido não é persistida. Ressalva: o intervalo hoje é de 3 h, não de minutos |
| Telas que carregam tudo | **refutada nesta escala** — `/devices` pagina no banco; pior tela soma 33 ms. Sobra só o filtro de faixa, por desenho |
| Reconstrução de chamadas a cada 60 s | **refutada** — 11–31 ms no caminho real; 717 ms só no reprocessamento total, que produção não faz |
| Retenção diária cara; `VACUUM` nunca roda | **metade confirmada** — a poda em si é barata hoje; o `VACUUM` que não roda custa **24,4 MB (44%) de arquivo inflado**, recuperáveis em 240 ms |

Achado que não estava na lista, e é o maior do caminho de request:
**recompilação de template por request**, ~8 ms em *toda* tela do sistema.

## 5. Etapa 2 — o que fazer, em ordem de retorno por risco

1. ✅ **Memorizar o `Jinja2Templates`** (`web/pages.py`). ~8 ms a menos em toda
   tela, ~80% do tempo do documento. Cuidado único: o `ChoiceLoader` com o cache
   de bundle (`core/resources.py`) precisa continuar montado.
2. ✅ **`VACUUM` depois da retenção**. Devolve 24,4 MB por 240 ms. Roda por
   limiar, não todo dia — o custo cresce com o tamanho do banco, e num dia de
   poda pequena a reescrita não se paga.
3. ✅ **Não varrer a tabela para mostrar o tamanho do ledger.**
   `SUM(payload_bytes)` é 2/3 do custo de `/api/mqtt/status`.
4. **Persistir duração de job.** Sem isso a §15.6 não fecha para `collect_extensions`
   e `monitor_devices`, e nenhum problema de campo é diagnosticável. O caminho
   mais curto é gravar cada execução (job, início, duração, resultado) numa
   tabela própria com retenção curta; alimentar os instrumentos que já existem em
   `core/metrics.py` é o caminho mais completo — hoje eles estão declarados e
   ninguém os alimenta.
5. **Separar o ledger do banco principal** — a mudança de arquitetura que a
   §15.6 imagina. Os números **não a justificam ainda**: nenhuma tela passava de
   33 ms antes dos itens 1–3, e agora nenhuma passa de 18 ms. O que a
   justificaria é contenção sob escrita, que esta medição não cobre. Medir isso
   antes: repetir a medição de telas com o coletor rodando.

## 5.1 Itens 1 a 3 aplicados — antes e depois

Mesma ferramenta, mesmo banco, mesma máquina, 7 repetições.

| Tela | Antes | Depois | |
|---|---:|---:|---:|
| Mensagens (ledger, 15 min) | 33 ms | **12 ms** | −64% |
| Ramais (lista) | 24 ms | **15 ms** | −38% |
| Configurações | 23 ms | **13 ms** | −43% |
| Detalhe do ramal | 22 ms | **14 ms** | −36% |
| Dashboard | 20 ms | **11 ms** | −45% |
| Backup | 19 ms | **9 ms** | −53% |
| Configurador (lista) | 17 ms | **8 ms** | −53% |
| Configurador (ambiente) | 17 ms | **6 ms** | −65% |
| Updates | 16 ms | **7 ms** | −56% |
| Chamadas (24 h) | 15 ms | **6 ms** | −60% |
| Logs | 14 ms | **5 ms** | −64% |
| Painel ao vivo | 14 ms | **5 ms** | −64% |
| Webhooks | 13 ms | **5 ms** | −62% |
| Coletas | 13 ms | **5 ms** | −62% |
| Configurador (execuções) | 13 ms | **5 ms** | −62% |
| Ramais (filtro de faixa) | 19 ms | 18 ms | — |

O documento HTML caiu de 10–11 ms para **2 ms** em todas as telas, e deixou de
ser a requisição mais cara de qualquer uma delas — agora quem aparece no topo é
sempre uma API, que é como deveria ser. A tela de **filtro de faixa** não mudou
porque não tem documento: é a chamada de API pura que pagina em memória, e ela
segue sendo a requisição mais cara do sistema (18 ms).

Na poda, o `VACUUM` fez o esperado no banco real:

```
retention_vacuum  compactou=True  antes_mb=55.4  depois_mb=29.6
                  liberado_mb=25.8  duracao_ms=253
```

O job de retenção passou de 18 ms para 235 ms **no dia em que compacta** — é o
preço, e ele só é pago quando há mais de 8 MB e mais de 20% do arquivo a
recuperar. Nos outros dias a poda continua custando dezenas de milissegundos.

## 6. O que esta medição não prova

- **Contenção.** Tudo aqui roda com a ingestão parada. A §15.6 suspeita que
  "toda consulta de tela concorre com a escrita do coletor" — isso continua sem
  medição, e é o pré-requisito do item 5 acima.
- **Rede.** Os dois jobs de rede ficaram de fora de propósito: medir
  `monitor_devices` daqui dispararia varredura em ~1.930 IPs do cliente, e o que
  se mediria seria timeout, não o tempo real.
- **Cliente vs. bancada.** O tempo é de servidor puro (ASGI em processo). Uvicorn,
  rede e navegador entram por cima disso.
- **Regime.** O ledger tem 4,9 dias numa retenção de 7. A projeção de 62 mil
  mensagens é projeção — vale refazer a medição quando o ciclo completo tiver
  fechado.
