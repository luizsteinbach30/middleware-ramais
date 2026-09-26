# ADR-0007 — Túnel de acesso web pelo canal do NOC

Data: 2026-09-25 · Status: aceito · No NOC: ADR 0025 · Contrato: `noc-workconnect/docs/CONTRATO-DO-AGENTE.md` §11

## Contexto

O canal do agente só levava tarefas de verbo fechado: ping, inventário, edição da planilha. Para mexer num
telefone, num PABX ou no painel de um USCall, alguém precisava estar na rede do cliente ou pedir acesso remoto a
uma máquina de lá. O pedido do dono (25/09): *"emular um acesso web, como se fosse um túnel, mas com a conexão
saindo apenas pelo lado do middleware"*, e depois *"acessar outros endereços também, exemplo os servidores de
USCall"*.

## Decisões

### 1. O middleware abre, o NOC só atende

A tarefa `abrir_acesso_web` chega pelo long-poll de sempre. O middleware confere o destino e abre um
**WebSocket de saída** em `agente/v1/tunel/{sessao}`, pelo mesmo canal mTLS e com o mesmo Bearer. Nada entra na
rede do cliente, e nenhuma porta nova é aberta. As requisições HTTP do navegador vêm por esse WebSocket em
frames JSON (`req`, `req.corpo`, `req.fim`, e na volta `resp`, `resp.corpo`, `resp.fim`, `erro`), e o
middleware faz a chamada real ao equipamento.

### 2. O destino é decidido aqui

Há dois tipos de destino:

- **`lan`**: o NOC informa endereço, porta e esquema. ~~Só IPv4 privado; loopback, link-local, endereço
  público, nome de host e a própria interface do middleware recusados.~~ **Emenda 2.14.2 (26/09):** vale
  **qualquer destino que esta máquina alcança** — IPv4 de qualquer faixa (inclusive `127.0.0.1` e a própria
  interface do middleware) ou nome de host resolvido pelo DNS daqui. Ver a emenda no fim.
- **`uscall`**: o NOC informa só o **nome** do servidor cadastrado aqui, e o endereço sai do cadastro local.
  Endereço público só vale assim, cadastrado por quem opera o middleware.

~~O túnel não vira proxy para a internet.~~ Revogado pela emenda de 26/09.

### 3. Acesso completo, inclusive a rede, por decisão do dono

A interface do equipamento permite mudar IP, VLAN e DNS, que é exatamente o que o canal remoto proíbe desde o
item 10 do `docs/AGENTE-NOC.md`. O dono escolheu acesso completo. É uma **exceção declarada**: vale só para o
túnel, e as tarefas continuam sem campo de rede. O verbo é de raio `ESCRITA_REVERSIVEL`, porque chamar de
leitura mentiria para quem decide aprovação e reentrega.

### 4. Nenhuma credencial é injetada

Quem abre digita a senha do equipamento na tela de login dele. O middleware não usa a senha web do ambiente.

### 5. O corpo da requisição é juntado antes de ir ao equipamento

Repassar em pedaços viraria `Transfer-Encoding: chunked`, e servidor web de telefone costuma não aceitar. O
corpo inteiro vai com `Content-Length`, com teto de 128 MB. A resposta volta em pedaços de 64 KB.

### 6. Sessão com fim

São no máximo 60 minutos, contados aqui também. O WebSocket reconecta até cinco vezes se cair, e para de
reconectar quando o NOC responde 404 ou 410 (a sessão acabou lá) ou 401 ou 403 (canal recusado). A mesma sessão
reentregue não abre de novo.

## Consequências

- Todo middleware enrolado declara o verbo no manifesto, sem chave local. A decisão de quem pode abrir fica no
  NOC (permissão e auditoria de cada requisição).
- A v1 não carrega WebSocket do próprio equipamento. Uma interface que dependa disso abre, mas sem as partes
  ao vivo.
- `websockets` passa a ser dependência explícita. Já vinha de carona pelo `uvicorn[standard]`.

## Emenda 2.14.2 (26/09) — o túnel é o navegador sentado nesta máquina

Pedido do dono, depois de testar em produção: *"quero literalmente emular uma conexão web pelo middleware, como
se eu estivesse naquela máquina"* — a começar pela interface do próprio middleware, que era recusada.

- **Destino:** qualquer IPv4 que não seja multicast, `0.0.0.0` ou difusão, ou nome de host (RFC 1123). O nome
  **não** é fixado num IP: com todo destino permitido, a fixação contra DNS rebinding não protege nada, e
  conectar pelo nome preserva o SNI e o virtual host. **Quem pode abrir continua sendo decisão do NOC**
  (permissão `equipamento.acesso_web`, auditoria de cada requisição) — esta ponta só recusa o que não é forma
  de endereço.
- **A interface do middleware pelo túnel:** o login continua exigido (conferido: `api/auth.py` não dá passe
  livre a `127.0.0.1`). Todo pedido do túnel leva `X-Noc-Tunel: <sessão>`, e o bloqueio por senha errada conta
  por sessão quando o pedido vem desta máquina — senão as tentativas de um operador travariam os outros e quem
  usa o painel aqui mesmo. De outro IP a marca é ignorada, para não virar um jeito de escapar do bloqueio.
- **Pedido repetido:** o que cai sem nenhum byte de resposta (a conexão parada que o aparelho fechou) vai de
  novo, uma vez, **qualquer método** — regra do Chromium (`ShouldResendRequest`). Só GET/HEAD/OPTIONS eram
  repetidos, e o menu de telefone que carrega por POST virava 502 ("Server disconnected without sending a
  response", reproduzido em teste). Keep-alive com o aparelho de 5 s para 2 s.
- **Tempos:** leitura 95 s e espera por conexão 30 s — abaixo dos 100 s da Cloudflare e dos 110 s do nginx do
  NOC, para quem desiste primeiro ser o agente, com mensagem, e não a borda com um 524.
- Continua para a v2 do protocolo (NOC ADR 0028): endereço por origem, WebSocket do equipamento, frames
  binários, janela por fluxo e limite de banda por sessão.
