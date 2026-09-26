# ADR-0008 — A atualização pedida pelo NOC

Data: 2026-09-25 · Status: aceito · No NOC: ADR 0027 · Contrato: `noc-workconnect/docs/CONTRATO-DO-AGENTE.md` §12 ·
Revê: a decisão de 03/08 em `domain/config/update_settings.py` ("só verifica e avisa")

## Contexto

Atualizar o middleware era abrir a tela de cada cliente e clicar. O NOC já mandava a versão desejada em todo
heartbeat, mas ela só aparecia na tela. O dono pediu (25/09) que os agentes se atualizem sozinhos *"conforme
mudar a versão deles no NOC… de forma natural em todos os clientes e sem causar problemas ao sistema"*. Ele
decidiu: **só sobe**, **janela definida no NOC (padrão 02h–05h)** e **botão "Atualizar agora" no NOC**.

## Decisões

1. **A decisão mora aqui** (`updater/automatico.py`). O NOC só anuncia `atualizacao { versaoDesejada, janela,
   atualizarAgoraPedidoEm }`. A regra inteira é uma função pura, `decidir()`, com um teste por trava.
2. **Instalar é o mesmo caminho do botão local** (`updater/instalar.py`). A rota `POST /api/system/update`
   passou a chamar a mesma função.
3. **Versão exata e só para cima.** A release é buscada pela versão (`release_for_version`), e não pela mais
   nova do canal. Desejada menor é `ACIMA_DA_DESEJADA`. No Linux, o `install.sh --if-newer` lê a versão de
   `update.request`, e o `is_newer` continua recusando descer.
4. **Só ocioso.** Nada começa com run de aplicação aberto, escrita remota do NOC em andamento, túnel de acesso
   web aberto ou restauração pendente.
5. **Frota espalhada** dentro da janela por um atraso fixo derivado do id do agente (até 60 min).
6. **Três tentativas por versão**, no máximo uma por hora. O "Atualizar agora" zera a contagem.
   `SEM_RELEASE` consulta o GitHub de novo só depois de uma hora.
7. **Windows ganhou verificação e volta.** O ajudante `.bat` guarda o `.exe` atual como `.bak`, sobe o novo e
   exige que `/api/system/healthz` responda com a versão nova em até 150 s. Se não responder, encerra o novo,
   devolve o `.bak` e sobe o antigo. O desfecho fica em `update_result.txt` e vira o próximo estado. Antes disso,
   uma release que não abrisse deixava o cliente sem middleware até alguém ir lá.
8. **O estado volta ao NOC só quando ele anunciou o campo.** O DTO do heartbeat do NOC recusa campo
   desconhecido. Se o NOC responder 400, o próximo heartbeat vai sem o campo.
9. **Chave local `update.auto_noc`**, ligada por padrão, na tela de atualizações. Desligada, o NOC vê
   `DESLIGADA`.

## Consequências

- Quem está na 2.13.x não tem este código. Cada cliente precisa de **uma** atualização manual até a primeira
  versão com a atualização automática; daí em diante ela segue sozinha.
- No Linux, o instalador que o cliente já tem (o da 2.13.x) ignora a versão do pedido e instala a mais nova
  estável. Isso ainda é "só sobe", e a partir da primeira atualização o instalador novo passa a valer.
- Windows desktop: sem a janela aberta numa sessão logada não há middleware rodando, e portanto nem heartbeat
  nem atualização.
- `/api/system/healthz` passou a dizer a versão.
