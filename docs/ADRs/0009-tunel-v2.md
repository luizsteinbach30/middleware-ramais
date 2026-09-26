# ADR-0009 — Túnel v2: origens, WebSocket, janela por fluxo e banda

Data: 2026-09-26 · Status: aceito · No NOC: ADR 0028 · Contrato: `noc-workconnect/docs/CONTRATO-DO-AGENTE.md` §11.6 ·
Emenda: ADR 0007

## Contexto

Com o túnel da 2.14 o dono pediu *"literalmente emular uma conexão web pelo middleware, como se eu estivesse
naquela máquina"*, e que fosse *"eficiente e leve, sem impactar"*. A 2.14.2 já levou o destino a "tudo que a máquina
alcança" e consertou o menu que perdia pedaços (repetição por conexão nova). Faltava: outro endereço de dentro
aberto a partir da página, o WebSocket do equipamento, e não disputar o link da loja com a telefonia.

## Decisões

1. **Negociação no handshake.** O agente pede `X-Tunel-Protocolo: 2`; se o NOC devolver o mesmo cabeçalho, a
   sessão é v2 e o `X-Tunel-Banda-Kbps` vira o balde. NOC antigo não devolve e a sessão segue na v1 — os testes
   antigos do túnel são a prova. Nada vai na tarefa: o executor recusa parâmetro desconhecido, e isso quebraria
   quem ainda está na 2.14.
2. **Regras puras em `tunel_protocolo.py`**: quadro binário, `Balde`, `host_interno`, `para_o_tunel`,
   `reescrever_links_v2`, `location_v2`. A `Sessao` só usa.
3. **Corpo em binário, com janela por fluxo** (256 KB iniciais; `janela` do NOC repõe). Um download grande não
   enche o canal na frente do menu da outra aba.
4. **Balde por sessão** (token bucket com dívida; rajada de até 64 KB ou ¼ s): tudo que sobe para o NOC passa por
   ele. Medido em teste: 600 KB a 2 Mbit/s levam ~2 s; sem o balde, instantâneo.
5. **Origens.** `req` com `origem` vai a outra origem, conferida pela mesma regra do destino da sessão. O link
   absoluto para outro endereço de dentro (ou o mesmo aparelho em outro esquema/porta) vira `/__tunel/ir?u=…`, que o
   NOC resolve; endereço público embutido fica direto. O http que redireciona para o https ganha a sua origem em
   vez de trocar o destino da sessão.
6. **WebSocket do equipamento** (`ws.abrir` → `websockets` do lado de cá, sem verificar certificado como o
   `httpx`; subprotocolo escolhido pelo equipamento volta em `ws.aberto`; mensagens em quadros `0x03`/`0x04`).
7. **Sem reuso por origem**: o equipamento que derruba conexão reaproveitada é marcado pela base
   (`esquema://host:porta`), não pela sessão.

## Consequências

- O NOC precisa do DNS curinga e de `tunel.subdominio_por_origem` ligado para as origens; sem isso a origem 0
  funciona como na 2.14.
- Provado com Chrome real + NOC real + este agente: WebSocket ecoando pelo túnel, link para outro aparelho abrindo
  no subdomínio `-1` com cookies isolados, e a sessão fechando com a aba.
