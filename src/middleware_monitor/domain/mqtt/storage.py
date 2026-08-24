"""Tamanho do ledger MQTT — quantas mensagens e quantos bytes de payload.

**Por que existe.** `SUM(payload_bytes)` sobre `mqtt_messages` é varredura da
tabela inteira: `EXPLAIN QUERY PLAN` devolve `SCAN mqtt_messages`, porque a
coluna não é indexada, e a medição de 2026-08-24 deu **10,8 ms** para 43 mil
linhas / 15,6 MB — dois terços do custo de `/api/mqtt/status`, a requisição mais
cara do sistema (`docs/design/PERF_BASELINE.md`).

E o status não é pedido de vez em quando: a tela de **Config → MQTT** o busca a
cada 5 s e a de **Mensagens** a cada 10 s, cada aba aberta por sua conta. Sem
cache, a varredura de 15,6 MB acontecia dezenas de vezes por minuto, disputando
o mesmo arquivo com a escrita do coletor.

**Só a soma tem cache; a contagem continua ao vivo.** Não é economia de esforço:
`COUNT(*)` custa 0 ms porque cai num índice de cobertura, e é justamente o
número que o operador fica olhando enquanto configura o broker pela primeira
vez ("já chegou alguma mensagem?"). Congelá-lo por um minuto seria trocar 0 ms
por uma tela que parece travada. O total de bytes é rótulo de ocupação, muda em
escala de minutos e ninguém percebe um minuto de atraso nele.

A poda invalida o cache ao apagar — a única coisa que muda o número de forma
brusca.

⚠️ **A poda por tamanho não usa este módulo.** `purge_messages_by_size` chama
`repository.payload_bytes_total` direto: ela decide quantas linhas apagar a
partir desse valor, então precisa do exato. Cache ali apagaria de menos ou de
mais.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from middleware_monitor.domain.mqtt import repository as repo

if TYPE_CHECKING:  # pragma: no cover - só para tipagem
    from sqlalchemy.orm import Session as DBSession

TTL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class TamanhoLedger:
    """Ocupação do ledger, como a tela de configuração mostra."""

    mensagens: int = 0
    payload_bytes: int = 0


_payload_bytes: int | None = None
_payload_bytes_at: float = 0.0


def invalidate() -> None:
    """Descarta o valor guardado. Chamado pela poda e pelos testes."""
    global _payload_bytes, _payload_bytes_at
    _payload_bytes = None
    _payload_bytes_at = 0.0


def tamanho(db: DBSession) -> TamanhoLedger:
    """Ocupação do ledger: contagem exata, bytes recalculados no máximo uma vez
    a cada `TTL_SECONDS`."""
    global _payload_bytes, _payload_bytes_at
    agora = time.monotonic()
    if _payload_bytes is None or agora - _payload_bytes_at >= TTL_SECONDS:
        _payload_bytes = repo.payload_bytes_total(db)
        _payload_bytes_at = agora
    return TamanhoLedger(
        mensagens=repo.count_messages(db),
        payload_bytes=_payload_bytes,
    )
