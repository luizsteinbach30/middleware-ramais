"""Se cada servidor USCall respondeu na última coleta — em memória, por processo.

O manifesto do NOC precisa distinguir "agente vivo com USCall fora" de "agente
vivo e USCall ok": sem isso, um ramal sem resposta parece problema do ramal. O
teste de conexão da tela baixa a lista inteira de ramais e é pesado demais para
rodar a cada heartbeat — então o dado vem da coleta que já roda.

**Em memória de propósito.** Depois de um reinício, a resposta é ``None`` (não
se sabe) até a primeira coleta — e não o resultado de ontem apresentado como de
agora.
"""

from __future__ import annotations

_ultima: dict[str, bool] = {}


def registrar(nome: str, ok: bool) -> None:
    _ultima[nome] = ok


def alcancavel(nome: str) -> bool | None:
    return _ultima.get(nome)


def reset_para_testes() -> None:
    _ultima.clear()
