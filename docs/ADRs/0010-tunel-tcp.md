# ADR 0010 — Fluxo TCP no túnel (RDP e SSH pelo NOC)

- **Status:** aceita (26/09/2026) · **Versão:** 2.16.0 · **NOC:** ADR 0029 (Documentação do cliente)

## Contexto

O dono precisa acessar os servidores da rede do cliente (Windows Server e Hyper-V por RDP, Linux por
SSH) com a mesma facilidade do acesso web pelo túnel. No NOC, o `guacd` (Apache Guacamole, a base do
Keeper Connection Manager) desenha a sessão no navegador — mas a conexão TCP até o servidor tem de
sair **daqui**, de dentro para fora, pelo WebSocket que o túnel já abre (é o modelo do Teleport:
nada entra na rede do cliente).

## Decisão

- **Sessão de tipo TCP:** a tarefa `abrir_acesso_web` aceita `tipoDeDestino: "tcp"` (com `destino` e
  `porta`, a mesma regra de endereço do acesso web). Não há verbo novo no catálogo; agente antigo
  recusa com "use lan ou uscall".
- **Só a sessão TCP carrega TCP, e só até o servidor da tarefa.** `tcp.abrir` numa sessão web é
  recusado — o NOC não escolhe outro destino no meio da sessão.
- **Quadros:** `tcp.abrir {f}` → o agente conecta (15 s) e responde `tcp.aberto` ou `erro`; os bytes
  andam em quadros binários **`0x05` TCP_DADOS** nos dois sentidos; `tcp.fechar` de qualquer lado.
- **Janela nos dois sentidos:** o que sobe gasta crédito (inicial 256 KiB, devolvido pelo NOC com
  `janela`); o que desce é escrito e, **depois do `drain`**, o agente devolve `janela` ao NOC. Sem o
  timeout de leitura do HTTP: RDP parado é normal. O balde de banda da sessão vale também.
- **Handshake:** o agente anuncia `X-Tunel-Recursos: tcp` (o NOC só abre RDP/SSH em quem anuncia) e
  aceita `X-Tunel-Duracao-S` do NOC (8 h para RDP/SSH), com teto de 12 h. Sem o cabeçalho, 60 min.
- A senha **nunca passa por aqui**: quem a injeta é o NOC, no `guacd`. O agente só leva bytes.

## Consequências

- Registro no log local: `noc_tunel_tcp_aberto` e `bytes_tcp` no `noc_tunel_encerrado`.
- Testes: `tests/unit/test_tunel_tcp.py` (eco, janela, fechamento dos dois lados, recusas, duração).
